import json
import threading
import time

import pytest

from workstation.agent.context import fit_context
from workstation.skills.store import Skill, SkillStore
from workstation.tasks.memory import TaskMemory
from workstation.tasks.runner import TaskManager


class FakeRegistry:
    def __init__(self):
        self.calls = []

    def openai_tools(self):
        return [{"type": "function", "function": {"name": "lookup", "description": "", "parameters": {}}}]

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        return {"is_error": False, "text": f"result-{len(self.calls)}"}


def call(name, **args):
    return f"<tool_call>{json.dumps({'name': name, 'arguments': args})}</tool_call>"


def wait_for(tasks, task_id, *statuses, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if tasks.tasks[task_id]["status"] in statuses:
            return tasks.tasks[task_id]
        time.sleep(0.02)
    raise TimeoutError(tasks.tasks[task_id]["status"])


@pytest.fixture
def build(make_manager, tmp_path):
    running = []

    def make(script, **kwargs):
        manager = make_manager(script, **kwargs)
        registry = FakeRegistry()
        tasks = TaskManager(manager, registry, SkillStore(tmp_path / "skills"), tmp_path / "tasks")
        tasks.start()
        running.append(tasks)
        return tasks, manager, registry

    yield make
    for tasks in running:
        tasks.stop()


def test_plan_notes_and_finish(build):
    script = [
        call("plan_set", steps=["look up", "write"]),
        call("lookup", q="a"),
        call("save_note", note="the value is result-1"),
        call("plan_check", step=1),
        call("plan_check", step=2),
        call("finish_task", summary="All done: result-1"),
    ]
    tasks, manager, registry = build(script)
    task = wait_for(tasks, tasks.create("Find the value")["id"], "completed", "failed")
    assert task["status"] == "completed"
    assert task["result"] == "All done: result-1"
    assert all(step["done"] for step in task["plan"])
    assert task["notes"] == ["the value is result-1"]
    assert registry.calls == [("lookup", {"q": "a"})]


def test_memory_reaches_the_model_on_every_step(build):
    tasks, manager, _ = build([call("plan_set", steps=["alpha step"]), call("save_note", note="remember me"), call("finish_task", summary="x")])
    wait_for(tasks, tasks.create("My goal")["id"], "completed")
    last_system = manager.backend.prompts[-1][0]["content"]
    assert "My goal" in last_system and "alpha step" in last_system and "remember me" in last_system


def test_goal_survives_when_history_is_trimmed():
    task = {"goal": "G", "plan": [], "notes": ["N"]}
    block = TaskMemory(task).inject([{"role": "user", "content": "x"}])[0]["content"]
    assert "G" in block and "N" in block


def test_stopping_with_open_steps_gets_a_nudge(build):
    tasks, manager, _ = build([call("plan_set", steps=["one"]), "I will do it soon.", call("finish_task", summary="ok")])
    task = wait_for(tasks, tasks.create("Goal")["id"], "completed")
    assert task["result"] == "ok"
    assert task["nudges"] == 1
    assert any("not done" in m["content"] for m in manager.backend.prompts[-1] if m["role"] == "user")


def test_plain_answer_without_a_plan_completes(build):
    tasks, manager, _ = build(["Thinking about it.", "The answer is 4."])
    task = wait_for(tasks, tasks.create("2+2?")["id"], "completed")
    assert task["result"] == "The answer is 4."
    assert len(manager.backend.prompts) == 2  # one reminder, then it is taken at its word


def test_step_limit_pauses_and_resume_continues(build):
    script = [call("lookup", q=str(i)) for i in range(2)] + ["forced stop", "reminded", call("finish_task", summary="resumed fine")]
    tasks, _, registry = build(script)
    task_id = tasks.create("Goal", max_steps=2)["id"]
    task = wait_for(tasks, task_id, "paused", "completed")
    assert task["status"] == "paused"
    assert tasks.resume(task_id)
    task = wait_for(tasks, task_id, "completed")
    assert task["result"] == "resumed fine"


def test_failure_is_recorded_and_resumable(build):
    tasks, manager, _ = build([])
    task_id = tasks.create("Goal")["id"]
    task = wait_for(tasks, task_id, "failed")
    assert task["error"]
    manager.backend.script.append(call("finish_task", summary="Recovered."))
    assert tasks.resume(task_id)
    assert wait_for(tasks, task_id, "completed")["result"] == "Recovered."


def test_restart_marks_running_tasks_interrupted_and_resume_works(build, make_manager, tmp_path):
    tasks, _, _ = build([call("lookup", q="a"), "unused"])
    task_id = tasks.create("Goal", max_steps=1)["id"]
    wait_for(tasks, task_id, "paused", "completed")

    saved = tmp_path / "tasks" / f"{task_id}.json"
    data = json.loads(saved.read_text(encoding="utf-8"))
    data["status"] = "running"
    data["history"].append({"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}]})
    saved.write_text(json.dumps(data), encoding="utf-8")

    manager = make_manager([call("finish_task", summary="Finished after the restart.")])
    restarted = TaskManager(manager, FakeRegistry(), SkillStore(tmp_path / "skills"), tmp_path / "tasks")
    restarted.start()
    try:
        assert restarted.tasks[task_id]["status"] == "interrupted"
        assert restarted.resume(task_id)
        task = wait_for(restarted, task_id, "completed")
        assert task["result"] == "Finished after the restart."
        prompt = manager.backend.prompts[0]
        assert not prompt[-2].get("tool_calls")  # the half-finished step is gone
        assert "interrupted" in prompt[-1]["content"]
    finally:
        restarted.stop()


def test_tasks_run_one_at_a_time_in_order(build):
    tasks, _, _ = build([call("finish_task", summary="first"), call("finish_task", summary="second")])
    a = tasks.create("A")["id"]
    b = tasks.create("B")["id"]
    assert wait_for(tasks, a, "completed")["result"] == "first"
    assert wait_for(tasks, b, "completed")["result"] == "second"


def test_cancel_running_task(build):
    tasks, manager, _ = build([call("lookup", q=str(i)) for i in range(40)])
    original = tasks.registry.call
    started = threading.Event()

    def slow(name, arguments):
        started.set()
        time.sleep(0.05)
        return original(name, arguments)

    tasks.registry.call = slow
    task_id = tasks.create("Goal")["id"]
    assert started.wait(5)
    assert tasks.cancel(task_id)
    task = wait_for(tasks, task_id, "cancelled")
    assert task["steps"] < 40
    tasks.registry.call = original
    manager.backend.script[:] = [call("finish_task", summary="picked up again")]
    assert tasks.resume(task_id)
    assert wait_for(tasks, task_id, "completed")["result"] == "picked up again"


def test_cancel_queued_task_never_runs(build):
    tasks, manager, _ = build([call("finish_task", summary="first")])
    manager.lock.acquire()  # holds task A in its first step while B waits in the queue
    a = tasks.create("A")["id"]
    b = tasks.create("B")["id"]
    assert tasks.cancel(b)
    manager.lock.release()
    wait_for(tasks, a, "completed")
    time.sleep(0.1)
    assert tasks.tasks[b]["status"] == "cancelled"
    assert tasks.tasks[b]["steps"] == 0


def test_enabled_skill_reaches_tasks(build, tmp_path):
    SkillStore(tmp_path / "skills").save(Skill("tone", "d", "Always sign with TOKEN123."))
    tasks, manager, _ = build([call("finish_task", summary="done")])
    wait_for(tasks, tasks.create("Goal")["id"], "completed")
    assert "TOKEN123" in manager.backend.prompts[0][0]["content"]


def test_view_returns_only_new_events(build):
    tasks, _, _ = build([call("finish_task", summary="done")])
    task_id = tasks.create("Goal")["id"]
    wait_for(tasks, task_id, "completed")
    seq =tasks.view(task_id)["seq"]
    assert tasks.view(task_id, seq)["events"] == []
    assert "history" not in tasks.view(task_id)


def count(messages, tools=None):
    return sum(len(str(m.get("content"))) for m in messages)


def test_trimming_a_long_run_keeps_the_goal_and_latest_step():
    messages = [{"role": "user", "content": "THE GOAL"}]
    for i in range(30):
        messages.append({"role": "assistant", "content": "", "tool_calls": [{"id": str(i)}]})
        messages.append({"role": "tool", "tool_call_id": str(i), "content": f"r{i}" + "x" * 200})
    fitted, used, dropped = fit_context(messages, None, count, 1500, 256)
    assert fitted[0]["content"] == "THE GOAL"
    assert fitted[-1]["tool_call_id"] == "29"
    assert fitted[-2]["role"] == "assistant"
    assert all(m["role"] != "tool" or fitted[i - 1]["role"] == "assistant" for i, m in enumerate(fitted))
    assert used <= 1500 - 256 and dropped > 0

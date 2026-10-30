import threading

from workstation.agent.context import clip_tool_result, fit_context
from workstation.agent.loop import run_agent
from workstation.engine.base import GenerationParams

TOOLS = [
    {"type": "function", "function": {"name": name, "description": "", "parameters": {}}}
    for name in ("find", "price")
]


def call(name, **args):
    import json

    return f"<tool_call>{json.dumps({'name': name, 'arguments': args})}</tool_call>"


def agent(manager, runner, tools=TOOLS, **kwargs):
    history = [{"role": "user", "content": "go"}]
    return list(run_agent(manager, history, tools, runner, GenerationParams(), threading.Event(), **kwargs))


def of_type(events, kind):
    return [e for e in events if e["type"] == kind]


def test_multi_step_chain_feeds_results_back(make_manager):
    manager = make_manager([call("find", q="kb"), call("price", sku="KB-1"), "It costs 5."])
    results = {"find": "KB-1", "price": "5"}
    events = agent(manager, lambda name, args: {"is_error": False, "text": results[name]})

    assert [e["name"] for e in of_type(events, "tool_call")] == ["find", "price"]
    assert "".join(e["text"] for e in of_type(events, "token")) == "It costs 5."
    system, *last_prompt = manager.backend.prompts[-1]
    assert system["role"] == "system"
    assert [m["role"] for m in last_prompt] == ["user", "assistant", "tool", "assistant", "tool"]
    assert last_prompt[2]["content"] == "KB-1"
    assert last_prompt[2]["tool_call_id"] == last_prompt[1]["tool_calls"][0]["id"]
    assert of_type(events, "done")[0]["finish_reason"] == "stop"


def test_batched_calls_run_one_at_a_time(make_manager):
    manager = make_manager([call("find", q="kb") + call("price", sku="INVENTED"), call("price", sku="KB-1"), "done"])
    ran = []
    agent(manager, lambda name, args: ran.append(args) or {"is_error": False, "text": "KB-1"})
    assert ran == [{"q": "kb"}, {"sku": "KB-1"}]
    assert len(manager.backend.prompts[1][-2]["tool_calls"]) == 1
    assert "price" in manager.backend.prompts[1][-1]["content"]


def test_plain_answer_runs_no_tools(make_manager):
    manager = make_manager(["Hello!"])
    called = []
    events = agent(manager, lambda *a: called.append(a))
    assert not called
    assert len(manager.backend.prompts) == 1
    assert of_type(events, "message")[0]["message"]["content"] == "Hello!"


def test_tool_error_goes_back_to_the_model(make_manager):
    manager = make_manager([call("find", q="x"), "Sorry, that failed."])

    def boom(name, args):
        raise RuntimeError("down")

    events = agent(manager, boom)
    result = of_type(events, "tool_result")[0]
    assert result["is_error"] and "down" in result["text"]
    assert len(manager.backend.prompts) == 2


def test_unknown_tool_lists_real_ones(make_manager):
    manager = make_manager([call("nope"), "ok"])
    events = agent(manager, lambda *a: {"is_error": False, "text": ""})
    assert "find" in of_type(events, "tool_result")[0]["text"]


def test_broken_json_is_reported_to_the_model(make_manager):
    manager = make_manager(['<tool_call>{"name": </tool_call>', "ok"])
    events = agent(manager, lambda *a: {"is_error": False, "text": ""})
    assert of_type(events, "tool_result")[0]["is_error"]


def test_repeated_call_stops_the_loop(make_manager):
    manager = make_manager([call("find", q="x")] * 2 + ["done"])
    runs = []
    agent(manager, lambda *a: runs.append(a) or {"is_error": False, "text": "r"})
    assert len(runs) == 1
    assert manager.backend.prompts[-1][-1]["content"].startswith("r\n")
    assert not manager.backend.tools_seen[-1]


def test_failed_call_may_be_retried(make_manager):
    manager = make_manager([call("find", q="x")] * 2 + ["done"])
    outcomes = [{"is_error": True, "text": "busy"}, {"is_error": False, "text": "r"}]
    runs = []
    agent(manager, lambda *a: runs.append(a) or outcomes.pop(0))
    assert len(runs) == 2


def test_step_limit_forces_an_answer_without_tools(make_manager):
    manager = make_manager([call("find", q=str(i)) for i in range(3)] + ["final"])
    events = agent(manager, lambda *a: {"is_error": False, "text": "r"}, max_steps=3)
    assert manager.backend.tools_seen[-1] is None
    assert "".join(e["text"] for e in of_type(events, "token")) == "final"


def test_model_without_tool_template_uses_prompt_fallback(make_manager):
    manager = make_manager(['```json\n{"name": "find", "arguments": {}}\n```', "done"], tools=False)
    events = agent(manager, lambda *a: {"is_error": False, "text": "found"})
    assert manager.backend.tools_seen == [None, None]
    assert manager.backend.prompts[0][0]["role"] == "system"
    assert manager.backend.prompts[1][-1]["role"] == "user"
    assert "found" in manager.backend.prompts[1][-1]["content"]
    assert of_type(events, "tool_result")


def test_cancel_stops_before_next_step(make_manager):
    manager = make_manager([call("find"), "never"])
    cancel = threading.Event()

    def runner(name, args):
        cancel.set()
        return {"is_error": False, "text": "r"}

    events = list(run_agent(manager, [{"role": "user", "content": "x"}], TOOLS, runner, GenerationParams(), cancel))
    assert len(manager.backend.prompts) == 1
    assert events[-1]["finish_reason"] == "cancelled"


def test_clip_keeps_head_and_tail():
    text = "A" * 5000 + "B" * 5000
    clipped = clip_tool_result(text, 1000)
    assert len(clipped) < 1000
    assert clipped.startswith("A") and clipped.endswith("B")


def count(messages, tools=None):
    return sum(len(m["content"]) for m in messages)


def test_fit_keeps_system_and_latest_turn():
    messages = [{"role": "system", "content": "s" * 10}]
    for i in range(10):
        messages += [{"role": "user", "content": "u" * 300}, {"role": "assistant", "content": "a" * 300}]
    messages.append({"role": "user", "content": "latest"})
    fitted, used, dropped = fit_context(messages, None, count, 2000, 256)
    assert fitted[0]["role"] == "system"
    assert fitted[-1]["content"] == "latest"
    assert fitted[1]["role"] == "user"
    assert used <= 2000 - 256 and dropped > 0


def test_fit_never_orphans_tool_results():
    messages = [
        {"role": "user", "content": "x" * 900},
        {"role": "assistant", "content": "", "tool_calls": []},
        {"role": "tool", "content": "y" * 900},
        {"role": "user", "content": "now"},
    ]
    fitted, _, _ = fit_context(messages, None, count, 800, 100)
    assert [m["role"] for m in fitted] == ["user"]


def test_fit_leaves_short_history_alone():
    messages = [{"role": "user", "content": "hi"}]
    fitted, _, dropped = fit_context(messages, None, count, 2000, 256)
    assert fitted == messages and dropped == 0

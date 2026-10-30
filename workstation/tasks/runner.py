import queue
import threading
import time
import uuid
from pathlib import Path
from typing import List, Optional

from ..agent.builtin_tools import WEB_SEARCH, tool_runner
from ..agent.loop import run_agent
from ..config import DATA_DIR, read_json, write_json
from ..engine.base import GenerationParams
from ..engine.manager import ModelManager
from .memory import MEMORY_TOOL_NAMES, MEMORY_TOOLS, TaskMemory

TASKS_DIR = DATA_DIR / "tasks"
DEFAULT_MAX_STEPS = 50
MAX_NUDGES = 3
EVENT_TEXT_LIMIT = 2000
EVENTS_KEPT = 1000

ACTIVE = ("queued", "running")
SUMMARY_FIELDS = ("id", "title", "status", "steps", "max_steps", "created", "updated", "error")
NO_PLAN_NUDGE = "Do not stop yet. Use your tools to do the work, or call finish_task with the result if it is complete."
RESUME_NOTE ="You were interrupted. Check the plan and notes, then carry on from where you stopped."


class TaskManager:
    """Runs long tasks one at a time, saving after every step so they can resume."""

    def __init__(self, manager: ModelManager, registry, skills, root: Path = None):
        self.manager = manager
        self.registry = registry
        self.skills = skills
        self.root = Path(root) if root else TASKS_DIR
        self.tasks = {}
        self.cancels = {}
        self.lock = threading.Lock()
        self.queue: queue.Queue = queue.Queue()
        self.thread = None
        self.on_task_end = None

    def any_active(self) -> bool:
        return any(t["status"] in ACTIVE for t in self.tasks.values())

    def load_saved(self) -> None:
        for path in self.root.glob("*.json"):
            task = read_json(path, None)
            if not task:
                continue
            if task["status"] in ACTIVE:
                task["status"] = "interrupted"
                self._save(task)
            self.tasks[task["id"]] = task

    def start(self) -> None:
        self.load_saved()
        self.thread =threading.Thread(target=self._work, daemon=True, name="task-runner")
        self.thread.start()

    def stop(self) -> None:
        for cancel in self.cancels.values():
            cancel.set()
        self.queue.put(None)

    def _save(self, task: dict) -> None:
        task["updated"] = time.time()
        write_json(self.root / f"{task['id']}.json", task)

    def _log(self, task: dict, kind: str, **fields) -> None:
        task["seq"] += 1
        task["events"].append({"seq": task["seq"], "time": time.time(), "type": kind, **fields})
        del task["events"][:-EVENTS_KEPT]

    def create(self, goal: str, max_steps: int = DEFAULT_MAX_STEPS, temperature: float = 0.2, system_prompt: Optional[str] = None) -> dict:
        task = {
            "id": uuid.uuid4().hex[:12],
            "title": goal.strip().splitlines()[0][:80],
            "goal": goal.strip(),
            "system_prompt": system_prompt,
            "status": "queued",
            "created": time.time(),
            "updated": time.time(),
            "steps": 0,
            "max_steps": max_steps,
            "temperature": temperature,
            "nudges": 0,
            "plan": [],
            "notes": [],
            "history": [{"role": "user", "content": goal.strip()}],
            "events": [],
            "seq": 0,
            "result": None,
            "error": None,
            "finished": False,
        }
        self.tasks[task["id"]] = task
        self._save(task)
        self.queue.put(task["id"])
        return task

    def summaries(self) -> List[dict]:
        tasks = sorted(self.tasks.values(), key=lambda t: t["created"], reverse=True)
        return [{k: t.get(k) for k in SUMMARY_FIELDS} for t in tasks]

    def view(self, task_id: str, after: int = 0) -> Optional[dict]:
        task = self.tasks.get(task_id)
        if not task:
            return None
        hidden = ("history", "events")
        return {
            **{k: v for k, v in task.items() if k not in hidden},
            "events": [e for e in task["events"] if e["seq"] > after],
        }

    def cancel(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task or task["status"] not in ACTIVE:
            return False
        if task["status"] == "queued":
            task["status"] = "cancelled"
            self._save(task)
        elif task_id in self.cancels:
            self.cancels[task_id].set()
        return True

    def resume(self, task_id: str, extra_steps: int = DEFAULT_MAX_STEPS) -> bool:
        task = self.tasks.get(task_id)
        if not task or task["status"] in ACTIVE or task["status"] == "completed":
            return False
        history = task["history"]
        # A crash can land between a call and its result; the model must not see half a step.
        while history and history[-1].get("tool_calls"):
            history.pop()
        history.append({"role": "user", "content": RESUME_NOTE})
        task.update(status="queued", error=None, nudges=0, max_steps=task["steps"] + extra_steps)
        self._log(task, "status", text="Resumed")
        self._save(task)
        self.queue.put(task_id)
        return True

    def delete(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task or task["status"] == "running":
            return False
        task["status"] = "deleted"  # a queued entry may still be waiting in the queue
        del self.tasks[task_id]
        (self.root / f"{task_id}.json").unlink(missing_ok=True)
        return True

    def _work(self) -> None:
        while True:
            task_id = self.queue.get()
            if task_id is None:
                return
            task = self.tasks.get(task_id)
            if task and task["status"] == "queued":
                self._run(task)

    def _tools(self) -> List[dict]:
        return MEMORY_TOOLS + [WEB_SEARCH] + self.registry.openai_tools()

    def _record(self, task: dict, event: dict) -> None:
        kind = event["type"]
        if kind == "context":
            task["steps"] += 1
            task["context"] = {"used": event["used"], "limit": event["limit"]}
            if event["dropped"]:
                self._log(task, "status", text=f"Left out {event['dropped']} older messages to fit the context")
        elif kind == "message":
            task["history"].append(event["message"])
            if event["message"]["role"] == "assistant" and event["message"]["content"]:
                self._log(task, "text", text=event["message"]["content"][:EVENT_TEXT_LIMIT])
            self._save(task)
        elif kind == "tool_call":
            self._log(task, "tool_call", name=event["name"], arguments=event["arguments"])
        elif kind == "tool_result":
            self._log(task, "tool_result", name=event["name"], is_error=event["is_error"], text=event["text"][:EVENT_TEXT_LIMIT])

    def _run_round(self, task: dict, memory: TaskMemory, cancel: threading.Event) -> None:
        external = tool_runner(self.registry)

        def run_tool(name: str, arguments: dict) -> dict:
            if name not in MEMORY_TOOL_NAMES:
                return external(name, arguments)
            result = memory.run(name, arguments)
            if task["finished"]:
                cancel.set()  # stops the loop, the run ends as completed rather than cancelled
            return result

        params = GenerationParams(temperature=task["temperature"], max_tokens=1024)
        if task["system_prompt"]:
            base = [{"role": "system", "content": task["system_prompt"]}] + task["history"]
        else:
            base = task["history"]
        budget = task["max_steps"] - task["steps"]
        for event in run_agent(self.manager, base, self._tools(), run_tool, params, cancel, budget, memory.inject):
            self._record(task, event)

    def _last_text(self, task: dict) -> str:
        for message in reversed(task["history"]):
            if message["role"] == "assistant" and message.get("content"):
                return message["content"]
        return ""

    def _settle(self, task: dict, memory: TaskMemory, user_cancel: threading.Event) -> bool:
        """Decide what follows a round. True means run another one."""
        if task["finished"]:
            task["status"] = "completed"
        elif user_cancel.is_set():
            task["status"] = "cancelled"
        elif task["steps"] >= task["max_steps"]:
            task["status"] = "paused"
            self._log(task, "status", text=f"Reached the limit of {task['max_steps']} steps")
        elif memory.open_steps() and task["nudges"] < MAX_NUDGES:
            task["nudges"] += 1
            remaining = "; ".join(memory.open_steps())
            task["history"].append({"role": "user", "content": f"Keep going, these steps are not done: {remaining}"})
            self._log(task, "status", text="Stopped early, asked to continue")
            return True
        elif not task["plan"] and task["nudges"] == 0:
            task["nudges"] += 1
            task["history"].append({"role": "user", "content": NO_PLAN_NUDGE})
            self._log(task, "status", text="Stopped without a plan, asked to continue")
            return True
        else:
            task["status"] = "completed"
            task["result"] = self._last_text(task)
        return False

    def _run(self, task: dict) -> None:
        user_cancel = threading.Event()
        self.cancels[task["id"]] = user_cancel
        task["status"] = "running"
        self._log(task, "status", text="Started")
        memory = TaskMemory(task, self._skills_prompt)
        try:
            while True:
                if not self.manager.backend:
                    raise RuntimeError("No chat model is loaded.")
                round_cancel = _Linked(user_cancel)
                self._run_round(task, memory, round_cancel)
                if not self._settle(task, memory, user_cancel):
                    break
                self._save(task)
        except Exception as err:
            task["status"] = "failed"
            task["error"] = str(err)
            print(f"[Tasks] {task['id']} failed: {err}")
        self.cancels.pop(task["id"], None)
        self._log(task, "status", text=f"Ended: {task['status']}")
        self._save(task)
        if self.on_task_end:
            self.on_task_end()

    def _skills_prompt(self) -> str:
        length = self.manager.backend.capabilities.context_length if self.manager.backend else 4096
        return self.skills.prompt(length)


class _Linked(threading.Event):
    """Set by the run itself, and also set whenever the user cancels."""

    def __init__(self, parent: threading.Event):
        super().__init__()
        self.parent = parent

    def is_set(self) -> bool:
        return super().is_set() or self.parent.is_set()

from typing import Callable, List

NOTES_KEPT = 30
NOTE_LIMIT = 600


def function_tool(name: str, description: str, properties: dict, required: List[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


MEMORY_TOOLS = [
    function_tool(
        "plan_set",
        "Write or replace the plan for this task as a list of short steps.",
        {"steps": {"type": "array", "items": {"type": "string"}, "description": "The steps, in order"}},
        ["steps"],
    ),
    function_tool(
        "plan_check",
        "Mark a plan step as done.",
        {"step": {"type": "integer", "description": "Step number, starting at 1"}},
        ["step"],
    ),
    function_tool(
        "save_note",
        "Save a fact or result you will need later. Older tool results get forgotten, notes do not.",
        {"note": {"type": "string", "description": "What to remember"}},
        ["note"],
    ),
    function_tool(
        "finish_task",
        "Finish the task once everything is done, giving the final result.",
        {"summary": {"type": "string", "description": "The final result for the user"}},
        ["summary"],
    ),
]
MEMORY_TOOL_NAMES = {t["function"]["name"] for t in MEMORY_TOOLS}


class TaskMemory:
    """Plan and notes kept outside the conversation, so they survive context trimming."""

    def __init__(self, task: dict, skills_prompt: Callable[[], str] = lambda: ""):
        self.task = task
        self.skills_prompt = skills_prompt

    def open_steps(self) -> List[str]:
        return [s["text"] for s in self.task["plan"] if not s["done"]]

    def run(self, name: str, arguments: dict) -> dict:
        return getattr(self, f"_{name}")(arguments)

    def _plan_set(self, arguments: dict) -> dict:
        steps = arguments.get("steps")
        if isinstance(steps, str):
            steps = [s.strip(" -") for s in steps.splitlines() if s.strip()]
        if not steps or not isinstance(steps, list):
            return {"is_error": True, "text": "Give the steps as a non-empty list."}
        self.task["plan"] = [{"text": str(s), "done": False} for s in steps]
        return {"is_error": False, "text": f"Plan saved with {len(steps)} steps. Start on step 1."}

    def _plan_check(self, arguments: dict) -> dict:
        try:
            step = self.task["plan"][int(arguments.get("step")) - 1]
        except (TypeError, ValueError, IndexError):
            return {"is_error": True, "text": f"There is no such step. The plan has {len(self.task['plan'])} steps."}
        step["done"] = True
        remaining = self.open_steps()
        return {"is_error": False, "text": f"Next: {remaining[0]}" if remaining else "All steps done. Call finish_task."}

    def _save_note(self, arguments: dict) -> dict:
        note = str(arguments.get("note", "")).strip()
        if not note:
            return {"is_error": True, "text": "The note is empty."}
        self.task["notes"] = (self.task["notes"] + [note[:NOTE_LIMIT]])[-NOTES_KEPT:]
        return {"is_error": False, "text": "Saved."}

    def _finish_task(self, arguments: dict) -> dict:
        self.task["result"] = str(arguments.get("summary", "")).strip()
        self.task["finished"] = True
        return {"is_error": False, "text": "Task finished."}

    def prompt(self) -> str:
        task = self.task
        lines = [
            "You are working through a long task on your own. Nobody will answer questions, so make reasonable "
            "decisions and keep going.",
            "Start by calling plan_set. Do one step at a time, call plan_check after each, and use save_note for "
            "anything you will need later. Call finish_task when everything is done.",
            "",
            f"# Task\n{task['goal']}",
        ]
        if task["plan"]:
            lines.append("\n# Plan")
            lines += [f"{i}. [{'x' if s['done'] else ' '}] {s['text']}" for i, s in enumerate(task["plan"], 1)]
        if task["notes"]:
            lines.append("\n# Notes")
            lines += [f"- {note}" for note in task["notes"]]
        skills = self.skills_prompt()
        if skills:
            lines.append(f"\n{skills}")
        return "\n".join(lines)

    def inject(self, messages: List[dict]) -> List[dict]:
        block = self.prompt()
        if messages and messages[0]["role"] == "system":
            return [{**messages[0], "content": f"{messages[0]['content']}\n\n{block}"}] + messages[1:]
        return [{"role": "system", "content": block}] + messages

"""What the model can do from a chat, and how it knows about it."""
from typing import List

from ..mcp.client import ServerConfig
from ..skills.store import Skill, slugify
from ..tasks.memory import function_tool
from .builtin_tools import USE_SKILL, WEB_SEARCH, tool_runner

APPROVAL_TOOLS = ("connect_mcp_server",)
MAX_LISTED_TOOLS = 25
CONNECT_WAIT = 20

CHAT_TOOLS = [
    function_tool(
        "start_task",
        "Start a long job in the background: many steps, research, or anything that should keep running on its own.",
        {"goal": {"type": "string", "description": "Everything needed to do the job, since the chat is not visible to it"}},
        ["goal"],
    ),
    function_tool(
        "task_status",
        "Check on background tasks. Leave out the id to get the latest one.",
        {"id": {"type": "string", "description": "Task id"}},
        [],
    ),
    function_tool(
        "create_skill",
        "Save a new skill so you keep a way of working for every later chat. Use it when the user teaches you something to remember.",
        {
            "name": {"type": "string", "description": "Short name"},
            "description": {"type": "string", "description": "One sentence on what it does and when to use it"},
            "instructions": {"type": "string", "description": "Clear steps to follow"},
        },
        ["name", "description", "instructions"],
    ),
    function_tool(
        "connect_mcp_server",
        "Connect a new MCP server to gain its tools. The user is asked to approve first.",
        {
            "name": {"type": "string", "description": "Short name for the server"},
            "url": {"type": "string", "description": "The server's http or https URL"},
        },
        ["name", "url"],
    ),
]


class ChatTools:
    def __init__(self, manager, registry, skills, tasks):
        self.manager = manager
        self.registry = registry
        self.skills = skills
        self.tasks = tasks
        self.external = tool_runner(registry, skills)

    def definitions(self) -> List[dict]:
        """Rebuilt on every step, so a server connected mid-chat is usable at once."""
        tools = [WEB_SEARCH] + CHAT_TOOLS + self.registry.openai_tools()
        return tools + [USE_SKILL] if self.skills.needs_lookup(self._length()) else tools

    def run(self, name: str, arguments: dict) -> dict:
        handler = getattr(self, f"_{name}", None)
        if name in {t["function"]["name"] for t in CHAT_TOOLS} and handler:
            return handler(arguments)
        return self.external(name, arguments)

    def _length(self) -> int:
        backend = self.manager.backend
        return backend.capabilities.context_length if backend else 4096

    def _start_task(self, arguments: dict) -> dict:
        goal = str(arguments.get("goal", "")).strip()
        if not goal:
            return {"is_error": True, "text": "Describe the task in goal."}
        task = self.tasks.create(goal)
        return {
            "is_error": False,
            "text": f"Task {task['id']} started in the background. Tell the user it is running. Do not wait for it.",
            "ui": {"type": "task", "id": task["id"]},
        }

    def _task_status(self, arguments: dict) -> dict:
        task_id = arguments.get("id")
        if not task_id:
            latest = self.tasks.summaries()
            if not latest:
                return {"is_error": False, "text": "There are no tasks yet."}
            task_id = latest[0]["id"]
        task = self.tasks.view(str(task_id))
        if not task:
            return {"is_error": True, "text": f"There is no task {task_id}."}
        done = sum(step["done"] for step in task["plan"])
        lines = [f"Task {task['id']}: {task['status']}, step {task['steps']} of {task['max_steps']}."]
        if task["plan"]:
            lines.append(f"Plan: {done} of {len(task['plan'])} steps done.")
        if task["result"]:
            lines.append(f"Result: {task['result']}")
        if task["error"]:
            lines.append(f"Error: {task['error']}")
        return {"is_error": False, "text": "\n".join(lines), "ui": {"type": "task", "id": task["id"]}}

    def _create_skill(self, arguments: dict) -> dict:
        name = slugify(str(arguments.get("name", "")))
        description = str(arguments.get("description", "")).strip()
        instructions = str(arguments.get("instructions", "")).strip()
        if not description or not instructions:
            return {"is_error": True, "text": "A skill needs a description and instructions."}
        self.skills.save(Skill(name, description, instructions))
        return {"is_error": False, "text": f"Skill '{name}' saved and on.", "ui": {"type": "skill", "name": name}}

    def _connect_mcp_server(self, arguments: dict) -> dict:
        name = slugify(str(arguments.get("name", "")))
        url = str(arguments.get("url", "")).strip()
        if not url.lower().startswith(("http://", "https://")):
            return {"is_error": True, "text": "Only http and https MCP servers can be connected from chat."}
        if name in self.registry.configs:
            return {"is_error": True, "text": f"A server named '{name}' already exists."}
        info = self.registry.add(ServerConfig(id=name, url=url), CONNECT_WAIT)
        if info["status"] != "connected":
            self.registry.remove(info["id"])
            return {"is_error": True, "text": f"Could not connect: {info['error'] or 'no answer'}."}
        names = ", ".join(tool["name"] for tool in info["tools"])
        return {"is_error": False, "text": f"Connected to '{info['id']}'. New tools are ready: {names}."}

    def _servers_text(self) -> str:
        lines = []
        for server in self.registry.describe_all():
            tools = [t["name"] for t in server["tools"] if t["enabled"]][:MAX_LISTED_TOOLS]
            state = ", ".join(tools) if server["status"] == "connected" else server["status"]
            lines.append(f"- {server['id']}: {state or 'no tools'}")
        return "\n".join(lines) or "- none yet"

    def prompt(self, tools_on: bool) -> str:
        """A live summary. It is rebuilt each step, so it always matches reality."""
        skills = self.skills.describe_all()
        known = "\n".join(f"- {s['name']}: {s['description']}" for s in skills if s["enabled"]) or "- none yet"
        lines = [
            "# Your abilities",
            f"You are the model {self.manager.model_id} on the user's LLM Workstation. "
            "When asked what you can do, answer from this section and do not claim anything else.",
            f"\nConnected MCP servers and their tools:\n{self._servers_text()}",
            f"\nSkills you know:\n{known}",
        ]
        if tools_on:
            lines.append(
                "\nYou decide when to use these. Search the web for current facts. For a big job with many steps, "
                "call start_task. To remember a way of working, call create_skill. "
                "If the user gives an MCP server URL, call connect_mcp_server."
            )
        else:
            lines.append("\nTools are switched off in this chat, so you cannot use any of these right now. Say so if asked.")
        return "\n".join(lines)

    def inject(self, messages: List[dict], tools_on: bool = True) -> List[dict]:
        block = "\n\n".join(p for p in (self.prompt(tools_on), self.skills.prompt(self._length())) if p)
        if messages and messages[0]["role"] == "system":
            return [{**messages[0], "content": f"{messages[0]['content']}\n\n{block}"}] + messages[1:]
        return [{"role": "system", "content": block}] + messages

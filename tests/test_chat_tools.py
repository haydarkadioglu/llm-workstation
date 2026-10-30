import json
import threading
import time
from types import SimpleNamespace

import pytest

from workstation.agent.approvals import Approvals
from workstation.agent.chat_tools import APPROVAL_TOOLS, ChatTools
from workstation.agent.loop import run_agent
from workstation.engine.base import GenerationParams
from workstation.skills.store import Skill, SkillStore
from workstation.tasks.runner import TaskManager


def call(tool, /, **args):
    return f"<tool_call>{json.dumps({'name': tool, 'arguments': args})}</tool_call>"


class Registry:
    """Stands in for MCP: connecting a server really adds a tool."""

    def __init__(self):
        self.configs = {}
        self.connected = []
        self.called = []

    def add(self, config, wait=0):
        self.configs[config.id] = config
        self.connected.append(config)
        return {"id": config.id, "status": "connected", "error": None, "tools": [{"name": "lookup"}]}

    def remove(self, server_id):
        self.configs.pop(server_id, None)

    def describe_all(self):
        return [
            {"id": c.id, "status": "connected", "tools": [{"name": "lookup", "enabled": True}]}
            for c in self.configs.values()
        ]

    def openai_tools(self):
        return [
            {"type": "function", "function": {"name": f"{c.id}__lookup", "description": "", "parameters": {}}}
            for c in self.configs.values()
        ]

    def call(self, name, arguments):
        self.called.append(name)
        return {"is_error": False, "text": "looked up"}


@pytest.fixture
def setup(make_manager, tmp_path):
    def build(script):
        manager = make_manager(script)
        registry = Registry()
        skills = SkillStore(tmp_path / "skills")
        tasks = TaskManager(manager, registry, skills, tmp_path / "tasks")
        return SimpleNamespace(manager=manager, registry=registry, skills=skills, tasks=tasks, tools=ChatTools(manager, registry, skills, tasks))

    return build


def chat(ctx, approvals=None, tools_on=True):
    return list(
        run_agent(
            ctx.manager,
            [{"role": "user", "content": "go"}],
            ctx.tools.definitions if tools_on else [],
            ctx.tools.run,
            GenerationParams(),
            threading.Event(),
            prepare=lambda history: ctx.tools.inject(history, tools_on),
            approvals=approvals,
        )
    )


def system_text(ctx, index=0):
    return ctx.manager.backend.prompts[index][0]["content"]


def test_model_is_told_what_is_connected_and_known(setup):
    ctx = setup(["ok"])
    ctx.registry.add(SimpleNamespace(id="shop"))
    ctx.skills.save(Skill("tone", "Keeps a friendly tone.", "Be kind."))
    chat(ctx)
    text = system_text(ctx)
    assert "shop: lookup" in text and "tone: Keeps a friendly tone." in text
    assert "fake/model" in text and "start_task" in text


def test_model_knows_when_tools_are_off(setup):
    ctx = setup(["ok"])
    chat(ctx, tools_on=False)
    assert "switched off" in system_text(ctx)
    assert not ctx.manager.backend.tools_seen[0]


def test_chat_can_start_a_background_task(setup):
    ctx = setup([call("start_task", goal="Audit prices"), "Started."])
    events = chat(ctx)
    created = list(ctx.tasks.tasks.values())
    assert created[0]["goal"] == "Audit prices" and created[0]["status"] == "queued"
    ui = next(e for e in events if e["type"] == "tool_result")["ui"]
    assert ui == {"type": "task", "id": created[0]["id"]}


def test_chat_can_check_on_a_task(setup):
    ctx = setup([call("task_status"), "Running."])
    task = ctx.tasks.create("Goal")
    events = chat(ctx)
    assert task["id"] in next(e for e in events if e["type"] == "tool_result")["text"]


def test_new_skill_is_known_on_the_very_next_step(setup):
    ctx = setup([call("create_skill", name="Weekly Report", description="Reports.", instructions="List wins."), "Saved."])
    events = chat(ctx)
    assert ctx.skills.get("weekly-report").instructions == "List wins."
    assert "weekly-report: Reports." in system_text(ctx, 1)
    assert "List wins." in system_text(ctx, 1)
    assert next(e for e in events if e["type"] == "tool_result")["ui"] == {"type": "skill", "name": "weekly-report"}


def test_empty_skill_is_refused(setup):
    ctx = setup([call("create_skill", name="x", description="", instructions=""), "ok"])
    chat(ctx)
    assert ctx.skills.all() == []


def test_large_skills_are_read_on_demand(setup):
    ctx = setup([call("use_skill", name="big"), "done"])
    ctx.manager.backend.capabilities.context_length = 200
    ctx.skills.save(Skill("big", "A long one.", "x" * 5000))
    events = chat(ctx)
    assert "xxxx" not in system_text(ctx, 0)
    assert "big: A long one." in system_text(ctx, 0)
    assert "xxxx" in next(e for e in events if e["type"] == "tool_result")["text"]


def approve_when_asked(approvals, allowed):
    def run():
        while not approvals.pending:
            time.sleep(0.01)
        approvals.answer(next(iter(approvals.pending)), allowed)

    threading.Thread(target=run, daemon=True).start()


def test_connecting_a_server_waits_for_approval_then_tools_work_at_once(setup):
    ctx = setup([call("connect_mcp_server", name="shop", url="https://mcp.example.com/mcp"), call("shop__lookup"), "Done."])
    approvals = Approvals(APPROVAL_TOOLS)
    approve_when_asked(approvals, True)
    events = chat(ctx, approvals)
    assert any(e["type"] == "approval" for e in events)
    assert ctx.registry.connected[0].url == "https://mcp.example.com/mcp"
    assert ctx.registry.called == ["shop__lookup"]
    assert "shop: lookup" in system_text(ctx, 1)


def test_declined_connection_never_happens(setup):
    ctx = setup([call("connect_mcp_server", name="shop", url="https://mcp.example.com/mcp"), "Understood."])
    approvals = Approvals(APPROVAL_TOOLS)
    approve_when_asked(approvals, False)
    events = chat(ctx, approvals)
    assert ctx.registry.connected == []
    assert "did not allow" in next(e for e in events if e["type"] == "tool_result")["text"]


def test_stopping_the_chat_ends_a_pending_approval():
    approvals = Approvals(APPROVAL_TOOLS)
    ticket = approvals.open()
    cancel = threading.Event()
    cancel.set()
    assert approvals.wait(ticket, cancel) is False
    assert not approvals.answer(ticket, True)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "npx evil", ""])
def test_only_web_urls_can_be_connected(setup, url):
    ctx = setup([])
    assert ctx.tools.run("connect_mcp_server", {"name": "x", "url": url})["is_error"]
    assert ctx.registry.connected == []


def test_existing_server_cannot_be_replaced(setup):
    ctx = setup([])
    ctx.registry.configs["shop"] = SimpleNamespace(id="shop")
    assert ctx.tools.run("connect_mcp_server", {"name": "shop", "url": "https://x.example"})["is_error"]
    assert ctx.registry.connected == []


def test_failed_connection_leaves_nothing_saved(setup):
    ctx = setup([])
    ctx.registry.add = lambda config, wait=0: {"id": config.id, "status": "reconnecting", "error": "refused", "tools": []}
    removed = []
    ctx.registry.remove = removed.append
    result = ctx.tools.run("connect_mcp_server", {"name": "x", "url": "https://x.example"})
    assert result["is_error"] and removed == ["x"]


def test_background_tasks_cannot_gain_abilities(setup):
    ctx = setup([])
    names = {t["function"]["name"] for t in ctx.tasks._tools()}
    assert not names & {"connect_mcp_server", "create_skill", "start_task"}


def test_ui_data_never_reaches_the_model(setup):
    from workstation.engine.template import normalize_messages

    clean = normalize_messages([{"role": "tool", "content": "x", "ui": {"type": "task"}, "is_error": False}])
    assert set(clean[0]) == {"role", "content"}

import json

import pytest
from fastapi.testclient import TestClient

from workstation.api import chat as chat_api
from workstation.config import Settings
from workstation.mcp.registry import McpRegistry
from workstation.server import create_app


@pytest.fixture
def client_for(make_manager, tmp_path, monkeypatch):
    monkeypatch.setattr(chat_api, "CHATS_DIR", tmp_path / "chats")

    def build(script, **kwargs):
        app = create_app(Settings(model=None, tunnel=False), McpRegistry(tmp_path / "mcp.json"))
        manager = make_manager(script, **kwargs)
        app.state.manager.backend = manager.backend
        app.state.manager.model_id = manager.model_id
        return TestClient(app)

    return build


TOOLS = [{"type": "function", "function": {"name": "get_price", "description": "", "parameters": {"type": "object"}}}]
CALL = '<tool_call>{"name": "get_price", "arguments": {"sku": "A"}}</tool_call>'


def test_models_lists_loaded_model(client_for):
    assert client_for(["x"]).get("/v1/models").json()["data"][0]["id"] == "fake/model"


def test_completion_without_tools(client_for):
    body = client_for(["Hello there"]).post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]}).json()
    assert body["choices"][0]["message"]["content"] == "Hello there"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def test_completion_returns_tool_calls_to_the_client(client_for):
    body = client_for([CALL]).post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}], "tools": TOOLS},
    ).json()
    choice = body["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] is None
    assert json.loads(choice["message"]["tool_calls"][0]["function"]["arguments"]) == {"sku": "A"}


def test_tool_choice_none_disables_tools(client_for):
    client = client_for([CALL])
    body = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}], "tools": TOOLS, "tool_choice": "none"},
    ).json()
    assert "<tool_call>" in body["choices"][0]["message"]["content"]


def test_tool_round_trip_messages_are_accepted(client_for):
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "1", "type": "function", "function": {"name": "get_price", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "1", "content": "5"},
    ]
    body = client_for(["It is 5."]).post("/v1/chat/completions", json={"messages": messages, "tools": TOOLS}).json()
    assert body["choices"][0]["message"]["content"] == "It is 5."


def sse_events(response):
    return [line[6:] for line in response.iter_lines() if line.startswith("data: ")]


def test_streaming_completion_with_usage(client_for):
    with client_for(["Hello there"]).stream(
        "POST",
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": True, "stream_options": {"include_usage": True}},
    ) as response:
        events = sse_events(response)
    assert events[-1] == "[DONE]"
    chunks = [json.loads(e) for e in events[:-1]]
    text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks if c["choices"])
    assert text == "Hello there"
    assert chunks[-1]["usage"]["total_tokens"] == 15


def test_streaming_tool_call(client_for):
    with client_for([CALL]).stream(
        "POST",
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}], "tools": TOOLS, "stream": True},
    ) as response:
        chunks = [json.loads(e) for e in sse_events(response)[:-1]]
    deltas = [c["choices"][0] for c in chunks]
    assert any("tool_calls" in d["delta"] for d in deltas)
    assert deltas[-1]["finish_reason"] == "tool_calls"


def test_no_model_is_503(client_for, tmp_path):
    client = TestClient(create_app(Settings(model=None, tunnel=False), McpRegistry(tmp_path / "m2.json")))
    assert client.post("/v1/chat/completions", json={"messages": []}).status_code == 503


def test_ui_chat_stream_emits_typed_events(client_for):
    with client_for(["<think>hm</think>Hi"]).stream(
        "POST", "/api/chat", json={"messages": [{"role": "user", "content": "yo"}], "system_prompt": "be nice"}
    ) as response:
        events = [json.loads(e) for e in sse_events(response)]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "context"
    assert "thinking" in kinds and "token" in kinds and kinds[-1] == "done"
    message = next(e for e in events if e["type"] == "message")["message"]
    assert message["content"] == "Hi" and message["thinking"] == "hm"


def test_chat_persistence(client_for):
    client = client_for(["x"])
    client.put("/api/chats/abc-1", json={"title": "T", "updated": 1, "messages": []})
    assert client.get("/api/chats").json()[0]["title"] == "T"
    assert client.get("/api/chats/abc-1").json()["id"] == "abc-1"
    client.delete("/api/chats/abc-1")
    assert client.get("/api/chats/abc-1").status_code == 404


def test_chat_id_cannot_escape_directory(client_for):
    assert client_for(["x"]).get("/api/chats/..%2F..%2Fsecret").status_code in (400, 404)


def test_video_needs_video_pipeline(client_for):
    response = client_for(["x"]).post("/api/video/generate", json={"prompt": "p"})
    assert response.status_code == 400

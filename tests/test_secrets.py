from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from huggingface_hub.errors import HfHubHTTPError

from workstation import secrets
from workstation.api import models as models_api
from workstation.config import Settings
from workstation.hub.resumable import _headers
from workstation.mcp.registry import McpRegistry
from workstation.server import create_app

TOKEN = "hf_secretvalue1234"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(secrets, "SECRETS_FILE", tmp_path / "secrets.json")
    monkeypatch.delenv("HF_TOKEN", raising=False)


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(Settings(model=None, tunnel=False), McpRegistry(tmp_path / "mcp.json")))


def fake_hub(monkeypatch, whoami):
    monkeypatch.setattr(models_api, "HfApi", lambda: SimpleNamespace(whoami=whoami))


def http_error(status):
    return HfHubHTTPError("failed", response=SimpleNamespace(status_code=status, headers={}, request=None))


def test_order_is_explicit_then_saved_then_environment(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "env")
    assert secrets.resolve_token() == "env"
    secrets.save_token("saved", "me")
    assert secrets.resolve_token() == "saved"
    assert secrets.resolve_token("  typed ") == "typed"
    assert secrets.resolve_token("   ") == "saved"


def test_no_token_anywhere_is_none():
    assert secrets.resolve_token() is None
    assert secrets.token_status()["source"] is None


def test_status_never_contains_the_token():
    secrets.save_token(TOKEN, "me")
    status = secrets.token_status()
    assert TOKEN not in str(status)
    assert status == {"source": "saved", "hint": "...1234", "account": "me"}


def test_saved_token_survives_and_can_be_removed():
    secrets.save_token(TOKEN, "me")
    assert secrets.saved_token() == TOKEN
    secrets.clear_token()
    assert secrets.saved_token() is None


def test_api_saves_a_valid_token_and_hides_it(client, monkeypatch):
    fake_hub(monkeypatch, lambda token: {"name": "ada"})
    saved = client.post("/api/hf/token", json={"token": f" {TOKEN} "})
    assert saved.status_code == 200 and TOKEN not in saved.text
    assert saved.json()["account"] == "ada"
    assert secrets.saved_token() == TOKEN
    assert TOKEN not in client.get("/api/hf/token").text
    assert client.delete("/api/hf/token").json()["source"] is None


def test_api_refuses_a_rejected_token(client, monkeypatch):
    def rejected(token):
        raise http_error(401)

    fake_hub(monkeypatch, rejected)
    response = client.post("/api/hf/token", json={"token": "bad"})
    assert response.status_code == 400
    assert secrets.saved_token() is None


def test_network_failure_is_not_called_a_bad_token(client, monkeypatch):
    def offline(token):
        raise OSError("no route")

    fake_hub(monkeypatch, offline)
    response = client.post("/api/hf/token", json={"token": TOKEN})
    assert response.status_code == 502
    assert secrets.saved_token() is None


@pytest.mark.parametrize("message", ["401 Client Error", "403 Forbidden", "GatedRepoError: access is restricted"])
def test_gated_errors_explain_the_next_step(message):
    assert "Settings" in secrets.friendly_error(message, None)
    assert "accept its terms" in secrets.friendly_error(message, "tok")


def test_other_errors_pass_through():
    assert secrets.friendly_error("disk full", None) == "disk full"


@pytest.mark.parametrize(
    "url,sent",
    [
        ("https://huggingface.co/a", True),
        ("https://cdn-lfs.huggingface.co/a", True),
        ("https://nothuggingface.co/a", False),
        ("https://huggingface.co.evil.example/a", False),
        ("https://example.com/huggingface.co", False),
    ],
)
def test_token_only_goes_to_hugging_face(url, sent):
    assert ("Authorization" in _headers(url, 0, "t")) is sent

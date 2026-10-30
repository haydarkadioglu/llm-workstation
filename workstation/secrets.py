import os
from pathlib import Path
from typing import Optional

from .config import DATA_DIR, read_json, write_json

SECRETS_FILE = DATA_DIR / "secrets.json"


def _read() -> dict:
    return read_json(SECRETS_FILE, {})


def saved_token() -> Optional[str]:
    return _read().get("hf_token")


def save_token(token: str, account: str) -> None:
    write_json(SECRETS_FILE, {**_read(), "hf_token": token, "hf_account": account})
    try:
        Path(SECRETS_FILE).chmod(0o600)
    except OSError:
        pass


def clear_token() -> None:
    data = _read()
    data.pop("hf_token", None)
    data.pop("hf_account", None)
    write_json(SECRETS_FILE, data)


def resolve_token(explicit: Optional[str] = None) -> Optional[str]:
    """A token typed for this load wins, then the saved one, then the environment."""
    return (explicit or "").strip() or saved_token() or os.environ.get("HF_TOKEN") or None


def token_status() -> dict:
    """What the UI may know. The token itself never leaves the server."""
    data = _read()
    token = data.get("hf_token")
    source = "saved" if token else "environment" if os.environ.get("HF_TOKEN") else None
    token = token or os.environ.get("HF_TOKEN")
    return {
        "source": source,
        "hint": f"...{token[-4:]}" if token else None,
        "account": data.get("hf_account"),
    }


def friendly_error(message: str, token: Optional[str]) -> str:
    """Turn a gated repo failure into the step that fixes it."""
    lower = message.lower()
    if not any(mark in lower for mark in ("401", "403", "gated", "restricted", "unauthorized")):
        return message
    if token:
        return "Your Hugging Face token has no access to this model. Open the model's page and accept its terms."
    return "This model needs a Hugging Face token. Add one in Settings, then load it again."

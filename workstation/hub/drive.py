import os
from pathlib import Path
from typing import List, Optional

from ..config import DATA_DIR

DRIVE_ROOT = "/content/drive/MyDrive"
DRIVE_LLM_DIR = "/content/drive/MyDrive/LLM-Workstation/models"
DRIVE_GGUF_DIR = "/content/drive/MyDrive/LLM-Workstation/gguf"

_use_drive = False


def is_drive_mounted() -> bool:
    return os.path.isdir(DRIVE_ROOT)


def cache_dir() -> Optional[str]:
    """Cache directory for downloads, None meaning the default HF cache."""
    return DRIVE_LLM_DIR if _use_drive and is_drive_mounted() else None


def url_models_dir() -> Path:
    """Where GGUF files fetched from plain links are kept."""
    return Path(DRIVE_GGUF_DIR) if cache_dir() else DATA_DIR / "models"


def set_drive_mode(enabled: bool) -> dict:
    global _use_drive
    if enabled:
        if not is_drive_mounted():
            return {"ok": False, "error": "Google Drive is not mounted. Run drive.mount('/content/drive') in Colab first."}
        os.makedirs(DRIVE_LLM_DIR, exist_ok=True)
    _use_drive = enabled
    return {"ok": True, "drive_path": cache_dir()}


def drive_model_ids() -> List[str]:
    if not os.path.isdir(DRIVE_LLM_DIR):
        return []
    results = []
    for entry in os.scandir(DRIVE_LLM_DIR):
        if entry.is_dir() and entry.name.startswith("models--"):
            results.append(entry.name[len("models--") :].replace("--", "/", 1))
    return results


def drive_status() -> dict:
    mounted = is_drive_mounted()
    return {
        "mounted": mounted,
        "drive_mode": _use_drive,
        "drive_path": cache_dir(),
        "cached_models": drive_model_ids() if mounted else [],
    }

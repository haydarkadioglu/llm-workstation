import os
import shutil
from pathlib import Path
from typing import List, Optional

from ..config import DATA_DIR

# Overridable so other synced folders work too, and so this can be tested without Colab.
DRIVE_ROOT = os.environ.get("LLM_WORKSTATION_DRIVE_ROOT", "/content/drive/MyDrive")
DRIVE_HOME = os.path.join(DRIVE_ROOT, "LLM-Workstation")
DRIVE_LLM_DIR = os.path.join(DRIVE_HOME, "models")
DRIVE_GGUF_DIR = os.path.join(DRIVE_HOME, "gguf")
BACKUP_DIR = os.path.join(DRIVE_HOME, "backup")
MAX_BROWSE_ENTRIES = 500

_use_drive = False


def is_drive_mounted() -> bool:
    return os.path.isdir(DRIVE_ROOT)


def is_colab() -> bool:
    return "COLAB_RELEASE_TAG" in os.environ or os.path.isdir("/content/sample_data")


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


def _space() -> dict:
    usage = shutil.disk_usage(DRIVE_ROOT)
    return {"free_gb": round(usage.free / 1024 ** 3, 1), "total_gb": round(usage.total / 1024 ** 3, 1)}


def drive_status() -> dict:
    mounted = is_drive_mounted()
    return {
        "mounted": mounted,
        "is_colab": is_colab(),
        "drive_mode": _use_drive,
        "drive_path": cache_dir(),
        "cached_models": drive_model_ids() if mounted else [],
        "space": _space() if mounted else None,
    }


def browse(relative: str = "") -> dict:
    """List folders and GGUF files, without ever leaving the Drive root."""
    root = Path(DRIVE_ROOT).resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError("That folder is outside Google Drive.")
    if not target.is_dir():
        raise FileNotFoundError(relative)

    folders, files = [], []
    for entry in sorted(os.scandir(target), key=lambda e: e.name.lower())[:MAX_BROWSE_ENTRIES]:
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            folders.append(entry.name)
        elif entry.name.lower().endswith(".gguf"):
            files.append({"name": entry.name, "path": entry.path, "size": entry.stat().st_size})

    path = "" if target == root else target.relative_to(root).as_posix()
    return {"path": path, "folders": folders, "files": files}

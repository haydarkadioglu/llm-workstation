"""Backs up skills, tasks, chats and settings to Drive, since Colab wipes the disk."""
import shutil
import threading
import time
from pathlib import Path
from typing import List, Optional

from ..config import DATA_DIR, read_json, write_json
from . import drive

FOLDERS = ("skills", "tasks", "chats")
FILES = ("mcp.json", "model_settings.json")
TOKEN_FILE = "secrets.json"
INFO_FILE = "backup.json"
SETTINGS_FILE = DATA_DIR / "drive.json"
AUTO_INTERVAL = 300


def _backup_root() -> Path:
    return Path(drive.BACKUP_DIR)


def _items(include_token: bool) -> List[str]:
    return list(FOLDERS) + list(FILES) + ([TOKEN_FILE] if include_token else [])


def _copy_item(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
    elif source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _fingerprint() -> list:
    """Changes when anything worth backing up changed."""
    state = []
    for name in _items(False):
        path = DATA_DIR / name
        files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
        state += [(str(f), f.stat().st_mtime, f.stat().st_size) for f in files if f.exists()]
    return state


def info() -> Optional[dict]:
    return read_json(_backup_root() / INFO_FILE, None)


def create(include_token: bool = False) -> dict:
    """Write a new backup beside the old one, then swap, so a failure keeps the old one."""
    if not drive.is_drive_mounted():
        raise RuntimeError("Google Drive is not mounted.")
    root = _backup_root()
    fresh = root.with_name(root.name + ".new")
    shutil.rmtree(fresh, ignore_errors=True)
    fresh.mkdir(parents=True)

    saved = []
    for name in _items(include_token):
        source = DATA_DIR / name
        if source.exists():
            _copy_item(source, fresh / name)
            saved.append(name)
    details = {"time": time.time(), "items": saved, "has_token": TOKEN_FILE in saved}
    write_json(fresh / INFO_FILE, details)

    shutil.rmtree(root, ignore_errors=True)
    fresh.rename(root)
    return details


def restore() -> dict:
    details = info()
    if not details:
        raise FileNotFoundError("No backup found in Drive.")
    root = _backup_root()
    for name in details["items"]:
        _copy_item(root / name, DATA_DIR / name)
    return details


def auto_enabled() -> bool:
    return read_json(SETTINGS_FILE, {}).get("auto_backup", False)


def set_auto(enabled: bool) -> None:
    write_json(SETTINGS_FILE, {**read_json(SETTINGS_FILE, {}), "auto_backup": enabled})


class AutoBackup:
    """Backs up every few minutes while switched on, skipping when nothing changed."""

    def __init__(self):
        self.stop_event = threading.Event()
        self.last_state = None
        self.last_error = None
        self.lock = threading.Lock()

    def run_once(self) -> bool:
        if not auto_enabled() or not drive.is_drive_mounted():
            return False
        with self.lock:
            state = _fingerprint()
            if state == self.last_state:
                return False
            try:
                # Never the token: a saved one must be backed up on purpose.
                create(include_token=False)
                self.last_state = state
                self.last_error = None
                return True
            except OSError as err:
                self.last_error = str(err)
                print(f"[Backup] Failed: {err}")
                return False

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="auto-backup").start()

    def _loop(self) -> None:
        while not self.stop_event.wait(AUTO_INTERVAL):
            self.run_once()

    def stop(self) -> None:
        self.stop_event.set()

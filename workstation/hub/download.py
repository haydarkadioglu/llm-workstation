import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

import psutil

from . import drive
from .download_worker import MARKER

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKER = [sys.executable, "-W", "ignore", "-m", "workstation.hub.download_worker"]
POLL_SECONDS = 0.2


class DownloadStopped(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason  # "pause" or "cancel"


def _kill_tree(process: subprocess.Popen) -> None:
    """A Windows venv python.exe is a launcher, the real interpreter is its child."""
    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    process.kill()
    for child in children:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(children, timeout=5)
    process.wait()


def _read_lines(process: subprocess.Popen, lines: queue.Queue) -> None:
    for line in process.stdout:
        if line.startswith(MARKER):
            lines.put(json.loads(line[len(MARKER) :]))
    lines.put(None)


def run_download(kind: str, model_id: str, token, state, worker=None) -> dict:
    """Download in a child process, mirroring progress into `state`.

    `state.stop_request` set to "pause" or "cancel" kills the child. Partial files stay
    in the cache, and the next download of the same model picks them up.
    """
    process = subprocess.Popen(
        worker or WORKER,
        cwd=REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    request = {"kind": kind, "model_id": model_id, "token": token, "drive": bool(drive.cache_dir())}
    process.stdin.write(json.dumps(request) + "\n")
    process.stdin.flush()

    lines: queue.Queue = queue.Queue()
    threading.Thread(target=_read_lines, args=(process, lines), daemon=True).start()
    try:
        while True:
            if state.stop_request:
                raise DownloadStopped(state.stop_request)
            try:
                message = lines.get(timeout=POLL_SECONDS)
            except queue.Empty:
                continue
            if message is None:
                raise RuntimeError("The download stopped unexpectedly.")
            if "result" in message:
                return message["result"]
            if "error" in message:
                raise RuntimeError(message["error"])
            state.loading_progress = message["pct"]
            state.loading_speed = message["speed"]
            speed = f" ({message['speed']})" if message["speed"] else ""
            state.status = f"Downloading: {message['pct']}%{speed}"
    finally:
        _kill_tree(process)

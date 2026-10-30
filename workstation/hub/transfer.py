"""Copying models between the local disk and Drive, one job at a time.

Drive's mount has no symlinks and reads slowly. So copies hold real files in
`snapshots/`, which huggingface_hub accepts, and loading goes through the local disk.
"""
import shutil
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from huggingface_hub import constants

from ..config import DATA_DIR
from ..engine.gguf import parse_gguf_id, shard_names
from . import drive
from .progress import format_speed

CHUNK = 8 * 1024 * 1024
PARTIAL_SUFFIX = ".partial"
COPY_ROOM = 1.05

Copy = Tuple[Path, Path, int]


def _latest_snapshot(repo: Path) -> Tuple[Path, str]:
    ref = repo / "refs" / "main"
    if ref.exists():
        revision = ref.read_text().strip()
        if (repo / "snapshots" / revision).is_dir():
            return repo / "snapshots" / revision, revision
    snapshots = sorted((repo / "snapshots").iterdir()) if (repo / "snapshots").is_dir() else []
    if not snapshots:
        raise FileNotFoundError(f"Nothing to copy from {repo.name}.")
    return snapshots[-1], snapshots[-1].name


def _repo_copies(model_id: str, source_root: Path, target_root: Path) -> List[Copy]:
    is_gguf = model_id.lower().endswith(".gguf")
    repo_id, wanted = (parse_gguf_id(model_id)[0], parse_gguf_id(model_id)[2]) if is_gguf else (model_id, None)
    folder = "models--" + repo_id.replace("/", "--")
    snapshot, revision = _latest_snapshot(source_root / folder)
    names = set(shard_names(wanted)) if wanted else None

    target = target_root / folder
    copies = []
    for path in sorted(snapshot.rglob("*")):
        relative = path.relative_to(snapshot).as_posix()
        if not path.is_file() or (names is not None and relative not in names):
            continue
        real = path.resolve()  # the local cache keeps blobs behind symlinks
        copies.append((real, target / "snapshots" / revision / relative, real.stat().st_size))
    if names is not None and not copies:
        raise FileNotFoundError(f"{wanted} is not in {source_root}.")

    target.joinpath("refs").mkdir(parents=True, exist_ok=True)
    ref = target / "refs" / "main"
    if not ref.exists():
        ref.write_text(revision)
    return copies


def _file_copies(path: Path, target_dir: Path) -> List[Copy]:
    sources = [path.parent / name for name in shard_names(path.name)]
    return [(s, target_dir / s.name, s.stat().st_size) for s in sources if s.exists()]


def plan_copies(model_id: str, to_drive: bool) -> Tuple[List[Copy], str]:
    """Work out what to copy. Returns (copies, the id to load the result by)."""
    path = Path(model_id)
    if path.is_file():
        target = Path(drive.DRIVE_GGUF_DIR) if to_drive else DATA_DIR / "models"
        copies = _file_copies(path, target)
        return copies, str(target / path.name)
    local, remote = Path(constants.HF_HUB_CACHE), Path(drive.DRIVE_LLM_DIR)
    source, target = (local, remote) if to_drive else (remote, local)
    return _repo_copies(model_id, source, target), model_id


def _check_space(copies: List[Copy]) -> None:
    needed = sum(size for _, dest, size in copies if not dest.exists())
    if not needed:
        return
    folder = copies[0][1].parent
    folder.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(folder).free
    if needed * COPY_ROOM > free:
        raise OSError(f"Not enough space: {needed / 1024 ** 3:.1f} GB needed, {free / 1024 ** 3:.1f} GB free.")


class Cancelled(Exception):
    pass


class TransferManager:
    def __init__(self):
        self.job: Optional[dict] = None
        self.cancel_event = threading.Event()

    def busy(self) -> bool:
        return bool(self.job and self.job["status"] == "running")

    def start(self, label: str, copies: List[Copy], on_done: Optional[Callable[[], None]] = None) -> dict:
        if self.busy():
            raise RuntimeError("Another copy is running.")
        _check_space(copies)
        self.cancel_event.clear()
        self.job = {
            "label": label,
            "status": "running",
            "pct": 0,
            "speed": "",
            "error": None,
        }
        threading.Thread(target=self._run, args=(copies, on_done), daemon=True).start()
        return self.job

    def cancel(self) -> bool:
        if not self.busy():
            return False
        self.cancel_event.set()
        return True

    def _copy_file(self, source: Path, dest: Path, tick: Callable[[int], None]) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + PARTIAL_SUFFIX)
        try:
            with open(source, "rb") as reader, open(partial, "wb") as writer:
                while chunk := reader.read(CHUNK):
                    if self.cancel_event.is_set():
                        raise Cancelled()
                    writer.write(chunk)
                    tick(len(chunk))
            partial.replace(dest)
        finally:
            partial.unlink(missing_ok=True)

    def _run(self, copies: List[Copy], on_done: Optional[Callable[[], None]]) -> None:
        job = self.job
        total = sum(size for _, _, size in copies) or 1
        done = sum(size for _, dest, size in copies if dest.exists() and dest.stat().st_size == size)
        fetched = 0
        started = time.time()

        def tick(count: int) -> None:
            nonlocal done, fetched
            done += count
            fetched += count
            job["pct"] = min(int(done / total * 100), 100)
            job["speed"] = format_speed(fetched / max(time.time() - started, 0.001))

        try:
            for source, dest, size in copies:
                if not (dest.exists() and dest.stat().st_size == size):
                    self._copy_file(source, dest, tick)
            job.update(status="done", pct=100, speed="")
        except Cancelled:
            job.update(status="cancelled", speed="")
            return
        except OSError as err:
            job.update(status="failed", error=str(err), speed="")
            return
        if on_done:
            on_done()

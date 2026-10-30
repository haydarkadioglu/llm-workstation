import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from workstation.engine import manager as manager_module
from workstation.engine.base import Capabilities, ChatBackend
from workstation.engine.manager import ModelManager
from workstation.hub import cache
from workstation.hub.download import DownloadStopped, run_download
from workstation.hub.resumable import _headers, fetch

SLOW_WORKER = textwrap.dedent(
    """
    import sys, time
    sys.stdin.readline()
    print("library noise on stdout", flush=True)
    for pct in range(100):
        print('@@{"pct": %d, "speed": "1 MB/s"}' % pct, flush=True)
        time.sleep(0.05)
    print('@@{"result": {"path": "done"}}', flush=True)
    """
)


class State:
    stop_request = None
    status = ""
    loading_progress = 0
    loading_speed = ""


def worker(code):
    return [sys.executable, "-c", code]


def test_progress_and_result_come_through():
    state = State()
    code = "import sys; sys.stdin.readline(); print('@@{\"pct\": 40, \"speed\": \"2 MB/s\"}', flush=True); print('@@{\"result\": {\"path\": \"x\"}}', flush=True)"
    assert run_download("gguf", "a/b/c.gguf", None, state, worker(code)) == {"path": "x"}
    assert state.loading_progress == 40
    assert state.status == "Downloading: 40% (2 MB/s)"


@pytest.mark.parametrize("reason", ["pause", "cancel"])
def test_stop_kills_the_worker_quickly(reason):
    state = State()
    threading.Timer(0.5, lambda: setattr(state, "stop_request", reason)).start()
    started = time.time()
    with pytest.raises(DownloadStopped) as stopped:
        run_download("gguf", "a/b/c.gguf", None, state, worker(SLOW_WORKER))
    assert stopped.value.reason == reason
    assert time.time() - started < 2


def test_worker_error_is_raised():
    code = "import sys; sys.stdin.readline(); print('@@{\"error\": \"gated repo\"}', flush=True)"
    with pytest.raises(RuntimeError, match="gated repo"):
        run_download("gguf", "a/b/c.gguf", None, State(), worker(code))


def test_crashed_worker_is_reported():
    with pytest.raises(RuntimeError, match="unexpectedly"):
        run_download("gguf", "a/b/c.gguf", None, State(), worker("import sys; sys.exit(3)"))


class FakeGguf(ChatBackend):
    def __init__(self, *args):
        self.capabilities = Capabilities()

    def stream(self, *args):
        return iter(())

    def count_tokens(self, *args):
        return 0


@pytest.fixture
def scripted(monkeypatch):
    """A manager whose downloads wait until told to stop or finish."""
    monkeypatch.setattr("workstation.engine.gguf.GgufBackend", FakeGguf)
    monkeypatch.setattr(manager_module, "_save_model_settings", lambda *a: None)
    monkeypatch.setattr(manager_module, "saved_model_settings", lambda m: {})
    discarded = []
    monkeypatch.setattr(cache, "discard_partial", discarded.append)
    release = threading.Event()
    tokens = []

    def fake_download(kind, model_id, token, state):
        tokens.append(token)
        state.loading_progress = 40
        while not release.is_set():
            if state.stop_request:
                raise DownloadStopped(state.stop_request)
            time.sleep(0.01)
        return {"path": "file.gguf", "size_mb": 1}

    monkeypatch.setattr(manager_module, "run_download", fake_download)
    return ModelManager(), release, discarded, tokens


def wait_state(manager, *states):
    for _ in range(500):
        if manager.load_state in states:
            return
        time.sleep(0.01)
    raise TimeoutError(manager.load_state)


def start(manager, token="tok"):
    thread = threading.Thread(target=manager.load, args=("a/b/c.gguf", token), daemon=True)
    thread.start()
    wait_state(manager, "downloading")
    return thread


def test_pause_then_resume_finishes_the_load(scripted):
    manager, release, discarded, tokens = scripted
    start(manager)
    assert manager.pause_load()
    wait_state(manager, "paused")
    assert manager.status == "Paused at 40%"
    assert manager.snapshot()["load_state"] == "paused"
    assert not discarded

    release.set()
    assert manager.resume_load()
    for _ in range(500):
        if manager.status == "Ready":
            break
        time.sleep(0.01)
    assert manager.status == "Ready" and manager.load_state == "idle"
    assert tokens == ["tok", "tok"]


def test_cancel_discards_partial_files(scripted):
    manager, _, discarded, _ = scripted
    thread = start(manager)
    assert manager.cancel_load()
    thread.join(5)
    assert manager.load_state == "idle" and manager.status == "No model loaded"
    assert discarded == ["a/b/c.gguf"]


def test_cancel_while_paused(scripted):
    manager, _, discarded, _ = scripted
    start(manager)
    manager.pause_load()
    wait_state(manager, "paused")
    assert manager.cancel_load()
    assert manager.load_state == "idle" and discarded == ["a/b/c.gguf"]
    assert not manager.resume_load()


def test_locked_partial_file_cannot_jam_the_manager(scripted, monkeypatch):
    manager, _, _, _ = scripted

    def locked(model_id):
        raise PermissionError("in use")

    monkeypatch.setattr(cache, "discard_partial", locked)
    thread = start(manager)
    manager.cancel_load()
    thread.join(5)
    assert manager.load_state == "idle"


def test_stopping_also_kills_grandchildren():
    psutil = pytest.importorskip("psutil")
    code = textwrap.dedent(
        """
        import subprocess, sys, time
        sys.stdin.readline()
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        print('@@{"pct": %d, "speed": ""}' % child.pid, flush=True)
        time.sleep(60)
        """
    )
    state = State()

    def stop_once_reported():
        while not state.loading_progress:
            time.sleep(0.02)
        state.stop_request = "cancel"

    threading.Thread(target=stop_once_reported, daemon=True).start()
    with pytest.raises(DownloadStopped):
        run_download("gguf", "a/b/c.gguf", None, state, worker(code))
    assert not psutil.pid_exists(state.loading_progress)


def test_controls_do_nothing_when_idle():
    manager = ModelManager()
    assert not manager.pause_load()
    assert not manager.resume_load()
    assert not manager.cancel_load()


def test_new_load_replaces_a_paused_one(scripted):
    manager, release, _, _ = scripted
    start(manager)
    manager.pause_load()
    wait_state(manager, "paused")
    release.set()
    assert manager.load("a/b/other.gguf")
    assert manager.paused_load is None and manager.model_id == "a/b/other.gguf"


class RangeHandler(BaseHTTPRequestHandler):
    payload = bytes(range(256)) * 400
    ignore_range = False
    requests = []

    def do_GET(self):
        header = self.headers.get("Range")
        type(self).requests.append(header)
        start = int(header[6:-1]) if header and not self.ignore_range else 0
        self.send_response(206 if start else 200)
        self.end_headers()
        self.wfile.write(self.payload[start:])

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    RangeHandler.requests = []
    RangeHandler.ignore_range = False
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/file"
    httpd.shutdown()


def test_fetch_continues_from_the_partial_file(server, tmp_path):
    dest = tmp_path / "blob"
    data = RangeHandler.payload
    (tmp_path / "blob.partial").write_bytes(data[:1000])
    counted = []
    fetch(server, dest, len(data), None, counted.append)
    assert dest.read_bytes() == data
    assert RangeHandler.requests == ["bytes=1000-"]
    assert sum(counted) == len(data) - 1000
    assert not (tmp_path / "blob.partial").exists()


def test_fetch_starts_over_when_range_is_ignored(server, tmp_path):
    RangeHandler.ignore_range = True
    dest = tmp_path / "blob"
    data = RangeHandler.payload
    (tmp_path / "blob.partial").write_bytes(b"x" * 1000)
    fetch(server, dest, len(data), None, lambda n: None)
    assert dest.read_bytes() == data


def test_fetch_keeps_partial_when_cut_short(server, tmp_path):
    with pytest.raises(IOError):
        fetch(server, tmp_path / "blob", len(RangeHandler.payload) + 50, None, lambda n: None)
    assert (tmp_path / "blob.partial").stat().st_size == len(RangeHandler.payload)
    assert not (tmp_path / "blob").exists()


def test_token_only_goes_to_huggingface():
    assert "Authorization" in _headers("https://huggingface.co/x", 0, "t")
    assert "Authorization" not in _headers("https://cdn.example.com/x", 0, "t")


def test_discard_partial_keeps_finished_files(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "_cache_dirs", lambda: [str(tmp_path)])
    repo = tmp_path / "models--a--b" / "blobs"
    repo.mkdir(parents=True)
    (repo / "x.incomplete").write_text("partial")
    (repo / "done").write_text("whole")
    cache.discard_partial("a/b/q.gguf")
    assert sorted(p.name for p in repo.iterdir()) == ["done"]


def test_discard_partial_removes_an_empty_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "_cache_dirs", lambda: [str(tmp_path)])
    repo = tmp_path / "models--a--b" / "blobs"
    repo.mkdir(parents=True)
    (repo / "x.incomplete").write_text("partial")
    cache.discard_partial("a/b/q.gguf")
    assert not (tmp_path / "models--a--b").exists()

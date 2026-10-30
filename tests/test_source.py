import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from workstation.engine.gguf import parse_gguf_id
from workstation.hub import cache, drive
from workstation.hub.source import parse_model_source

REPO = "bartowski/Qwen-GGUF"


@pytest.mark.parametrize(
    "text,model_id",
    [
        (f"https://huggingface.co/{REPO}/blob/main/q4.gguf", f"{REPO}/q4.gguf"),
        (f"https://huggingface.co/{REPO}/resolve/main/q4.gguf?download=true", f"{REPO}/q4.gguf"),
        (f"https://huggingface.co/{REPO}/resolve/main/sub/dir/q4.gguf", f"{REPO}/sub/dir/q4.gguf"),
        (f"https://hf.co/{REPO}/blob/main/q4.gguf", f"{REPO}/q4.gguf"),
        (f"https://www.huggingface.co/{REPO}/blob/main/q4.gguf", f"{REPO}/q4.gguf"),
        (f"  https://huggingface.co/{REPO}/blob/main/q4.gguf  ", f"{REPO}/q4.gguf"),
        (f"https://huggingface.co/{REPO}/raw/main/my%20model.gguf", f"{REPO}/my model.gguf"),
        (f"https://huggingface.co/{REPO}/resolve/abc123/q4.gguf", f"{REPO}@abc123/q4.gguf"),
        (f"{REPO}/q4.gguf", f"{REPO}/q4.gguf"),
    ],
)
def test_gguf_links(text, model_id):
    source = parse_model_source(text)
    assert source.kind == "gguf" and source.model_id == model_id


def test_pinned_revision_is_kept():
    assert parse_gguf_id(f"{REPO}@abc123/sub/q4.gguf") == (REPO, "abc123", "sub/q4.gguf")
    assert parse_gguf_id(f"{REPO}/q4.gguf") == (REPO, None, "q4.gguf")


def test_repo_page_is_a_repo():
    source = parse_model_source("https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct")
    assert (source.kind, source.model_id, source.needs_file_choice) == ("repo", "Qwen/Qwen2.5-1.5B-Instruct", False)


def test_tree_view_is_a_repo():
    assert parse_model_source("https://huggingface.co/a/b/tree/main").model_id == "a/b"


def test_gguf_repo_page_asks_for_a_file():
    assert parse_model_source(f"https://huggingface.co/{REPO}").needs_file_choice


def test_link_from_another_site():
    url = "https://models.example.com/files/q4.gguf?sig=abc"
    source = parse_model_source(url)
    assert (source.kind, source.model_id) == ("url", url)


def test_plain_id_and_local_path_stay_as_they_are():
    assert parse_model_source("Qwen/Qwen2.5-1.5B-Instruct").kind == "repo"
    assert parse_model_source("/content/models/q4.gguf").kind == "gguf"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "https://example.com/page.html",
        "https://huggingface.co/datasets/a/b",
        "https://huggingface.co/spaces/a/b",
        "https://huggingface.co/just-a-user",
        f"https://huggingface.co/{REPO}/blob/main/README.md",
    ],
)
def test_unusable_input_is_refused(text):
    with pytest.raises(ValueError):
        parse_model_source(text)


class Files(BaseHTTPRequestHandler):
    content = {}
    send_length = True

    def do_HEAD(self):
        self.reply(head=True)

    def do_GET(self):
        self.reply()

    def reply(self, head=False):
        body = self.content.get(self.path.split("?")[0])
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        if self.send_length:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setattr(drive, "url_models_dir", lambda: tmp_path / "models")
    monkeypatch.setattr(cache, "DATA_DIR", tmp_path)
    Files.content = {}
    Files.send_length = True
    server = ThreadingHTTPServer(("127.0.0.1", 0), Files)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


GGUF = b"GGUF" + b"x" * 2000


def nothing(*args):
    pass


def test_link_downloads_and_shows_up_as_a_model(site):
    Files.content["/m.gguf"] = GGUF
    path, _ = cache.download_url(f"{site}/m.gguf?sig=1", nothing, nothing)
    assert open(path, "rb").read() == GGUF
    listed = cache._url_models()
    assert listed[0]["name"] == "m.gguf" and listed[0]["id"] == path
    assert cache.delete_cached(path)
    assert cache._url_models() == []


def test_signed_links_to_the_same_file_share_a_partial(site):
    first = cache._url_targets(f"{site}/m.gguf?sig=1")[0][1]
    second = cache._url_targets(f"{site}/m.gguf?sig=2")[0][1]
    assert first == second


def test_web_page_instead_of_a_model_is_rejected(site):
    Files.content["/m.gguf"] = b"<html>please log in</html>"
    with pytest.raises(ValueError, match="GGUF"):
        cache.download_url(f"{site}/m.gguf", nothing, nothing)
    assert cache._url_models() == []


def test_split_file_fetches_every_part(site):
    Files.content["/m-00001-of-00002.gguf"] = GGUF
    Files.content["/m-00002-of-00002.gguf"] = GGUF
    cache.download_url(f"{site}/m-00001-of-00002.gguf", nothing, nothing)
    listed = cache._url_models()
    assert len(listed) == 1
    assert listed[0]["size_gb"] == 0
    assert cache.delete_cached(listed[0]["id"])
    assert not list(cache.drive.url_models_dir().iterdir())


def test_unknown_size_warns_and_still_downloads(site):
    Files.content["/m.gguf"] = GGUF
    Files.send_length = False
    notes = []
    path, _ = cache.download_url(f"{site}/m.gguf", nothing, notes.append)
    assert notes and open(path, "rb").read() == GGUF


def test_cancel_removes_the_partial_file(site):
    url = f"{site}/m.gguf"
    target = cache._url_targets(url)[0][1]
    target.parent.mkdir(parents=True)
    partial = target.with_name(target.name + ".partial")
    partial.write_bytes(b"x")
    cache.discard_partial(url)
    assert not partial.exists()


def test_only_listed_files_can_be_deleted(site, tmp_path):
    outside = tmp_path / "keep.gguf"
    outside.write_bytes(GGUF)
    assert not cache.delete_cached(str(outside))
    assert outside.exists()


def test_bad_link_fails_cleanly_in_the_manager():
    from workstation.engine.manager import ModelManager

    manager = ModelManager()
    assert not manager.load("https://example.com/page.html")
    assert manager.status == "Error loading model" and "gguf" in manager.error_message.lower()
    assert manager.load_state == "idle"


def test_gguf_link_is_refused_on_the_image_tab():
    from workstation.engine.manager import ModelManager

    manager = ModelManager()
    assert not manager.load(f"https://huggingface.co/{REPO}/blob/main/q4.gguf", kind="image")
    assert "chat" in manager.error_message.lower()
    time.sleep(0)

import json
import os
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from huggingface_hub import scan_cache_dir

from workstation import config
from workstation.hub import backup, cache, drive, transfer
from workstation.hub.transfer import TransferManager, plan_copies

REPO = "models--org--model"
REVISION = "abc123"


def wait_job(manager, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if manager.job["status"] != "running":
            return manager.job
        time.sleep(0.01)
    raise TimeoutError


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A Drive root, a local HF cache and a data folder, all under tmp_path."""
    root = tmp_path / "MyDrive"
    root.mkdir()
    home = root / "LLM-Workstation"
    data = tmp_path / "data"
    data.mkdir()
    local = tmp_path / "hub"
    local.mkdir()
    monkeypatch.setattr(drive, "DRIVE_ROOT", str(root))
    monkeypatch.setattr(drive, "DRIVE_LLM_DIR", str(home / "models"))
    monkeypatch.setattr(drive, "DRIVE_GGUF_DIR", str(home / "gguf"))
    monkeypatch.setattr(drive, "BACKUP_DIR", str(home / "backup"))
    monkeypatch.setattr(drive, "DATA_DIR", data)
    monkeypatch.setattr(backup, "DATA_DIR", data)
    monkeypatch.setattr(backup, "SETTINGS_FILE", data / "drive.json")
    monkeypatch.setattr(transfer, "DATA_DIR", data)
    monkeypatch.setattr(cache, "DATA_DIR", data)
    monkeypatch.setattr(transfer.constants, "HF_HUB_CACHE", str(local))
    monkeypatch.setattr(cache.constants, "HF_HUB_CACHE", str(local))
    return tmp_path


def make_repo(root: Path, files: dict, repo=REPO, symlinks=True) -> Path:
    """A real HF cache layout, with snapshot files behind blobs when the OS allows."""
    folder = root / repo
    (folder / "refs").mkdir(parents=True)
    (folder / "refs" / "main").write_text(REVISION)
    (folder / "blobs").mkdir()
    snapshot = folder / "snapshots" / REVISION
    snapshot.mkdir(parents=True)
    for index, (name, body) in enumerate(files.items()):
        blob = folder / "blobs" / f"blob{index}"
        blob.write_bytes(body)
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if not symlinks:
                raise OSError
            os.symlink(blob, target)
        except OSError:
            shutil.copy(blob, target)
    return folder


def test_status_when_not_mounted(tmp_path, monkeypatch):
    monkeypatch.setattr(drive, "DRIVE_ROOT", str(tmp_path / "missing"))
    status = drive.drive_status()
    assert status["mounted"] is False and status["space"] is None
    assert not drive.set_drive_mode(True)["ok"]


def test_status_when_mounted(fake):
    status = drive.drive_status()
    assert status["mounted"] and status["space"]["total_gb"] > 0


def test_browse_shows_folders_and_gguf_only(fake):
    root = Path(drive.DRIVE_ROOT)
    (root / "models").mkdir()
    (root / ".hidden").mkdir()
    (root / "a.gguf").write_bytes(b"GGUF")
    (root / "photo.jpg").write_bytes(b"x")
    listing = drive.browse()
    assert listing["folders"] == ["models"]
    assert [f["name"] for f in listing["files"]] == ["a.gguf"]
    assert drive.browse("models")["path"] == "models"


@pytest.mark.parametrize("escape", ["..", "../..", "models/../../.."])
def test_browse_cannot_leave_drive(fake, escape):
    (Path(drive.DRIVE_ROOT) / "models").mkdir()
    with pytest.raises(ValueError):
        drive.browse(escape)


def test_browse_blocks_absolute_paths(fake):
    with pytest.raises(ValueError):
        drive.browse(str(fake))


def test_whole_repo_copies_to_drive_as_real_files(fake):
    make_repo(Path(transfer.constants.HF_HUB_CACHE), {"config.json": b"{}", "sub/model.safetensors": b"w" * 50})
    copies, load_id = plan_copies("org/model", to_drive=True)
    manager = TransferManager()
    manager.start("model", copies)
    assert wait_job(manager)["status"] == "done"
    assert load_id == "org/model"

    copied = Path(drive.DRIVE_LLM_DIR) / REPO / "snapshots" / REVISION
    assert (copied / "sub/model.safetensors").read_bytes() == b"w" * 50
    assert not (copied / "sub/model.safetensors").is_symlink()
    assert (Path(drive.DRIVE_LLM_DIR) / REPO / "refs/main").read_text() == REVISION


def test_copy_in_drive_is_seen_as_a_cached_model(fake):
    make_repo(Path(transfer.constants.HF_HUB_CACHE), {"config.json": b"{}", "m.safetensors": b"w" * 50})
    copies, _ = plan_copies("org/model", to_drive=True)
    manager = TransferManager()
    manager.start("model", copies)
    wait_job(manager)
    repos = scan_cache_dir(drive.DRIVE_LLM_DIR).repos
    assert [r.repo_id for r in repos] == ["org/model"]
    assert any(m["id"] == "org/model" and m["location"] == "drive" for m in cache.list_cached())


def test_one_gguf_copies_alone_with_its_parts(fake):
    files = {"a-00001-of-00002.gguf": b"1", "a-00002-of-00002.gguf": b"2", "other.gguf": b"3"}
    make_repo(Path(transfer.constants.HF_HUB_CACHE), files)
    copies, _ = plan_copies("org/model/a-00001-of-00002.gguf", to_drive=True)
    assert sorted(dest.name for _, dest, _ in copies) == ["a-00001-of-00002.gguf", "a-00002-of-00002.gguf"]


def test_loose_file_comes_to_local_disk(fake):
    source = Path(drive.DRIVE_ROOT) / "my.gguf"
    source.write_bytes(b"GGUF" * 10)
    copies, load_id = plan_copies(str(source), to_drive=False)
    manager = TransferManager()
    done = []
    manager.start("my", copies, lambda: done.append(True))
    assert wait_job(manager)["status"] == "done"
    deadline = time.time() + 5
    while not done and time.time() < deadline:
        time.sleep(0.01)
    assert Path(load_id).read_bytes() == source.read_bytes() and done


def test_missing_model_reports_clearly(fake):
    with pytest.raises(OSError):
        plan_copies("org/nothing", to_drive=True)


def test_no_room_is_refused_before_copying(fake, monkeypatch):
    source = Path(drive.DRIVE_ROOT) / "my.gguf"
    source.write_bytes(b"x" * 100)
    monkeypatch.setattr(transfer.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    copies, _ = plan_copies(str(source), to_drive=False)
    with pytest.raises(OSError, match="Not enough space"):
        TransferManager().start("my", copies)


def test_cancel_leaves_no_partial_file(fake, monkeypatch):
    monkeypatch.setattr(transfer, "CHUNK", 8)
    source = Path(drive.DRIVE_ROOT) / "big.gguf"
    source.write_bytes(b"x" * 80000)
    copies, load_id = plan_copies(str(source), to_drive=False)
    manager = TransferManager()
    original = manager._copy_file

    def slow(src, dest, tick):
        def slow_tick(count):
            tick(count)
            time.sleep(0.001)

        original(src, dest, slow_tick)

    manager._copy_file = slow
    manager.start("big", copies)
    assert manager.cancel()
    assert wait_job(manager)["status"] == "cancelled"
    assert not Path(load_id).exists()
    assert not list(Path(load_id).parent.glob("*.partial"))


def test_second_copy_waits_for_the_first(fake):
    manager = TransferManager()
    manager.job = {"status": "running"}
    with pytest.raises(RuntimeError):
        manager.start("x", [])


def test_delete_picks_the_requested_copy(fake):
    make_repo(Path(transfer.constants.HF_HUB_CACHE), {"config.json": b"{}"})
    make_repo(Path(drive.DRIVE_LLM_DIR), {"config.json": b"{}"}, symlinks=False)
    assert cache.delete_cached("org/model", "drive")
    assert not (Path(drive.DRIVE_LLM_DIR) / REPO).exists()
    assert (Path(transfer.constants.HF_HUB_CACHE) / REPO).exists()


def seed_data():
    data = backup.DATA_DIR
    (data / "skills").mkdir()
    (data / "skills" / "tone.md").write_text("be kind")
    (data / "chats").mkdir()
    (data / "chats" / "c1.json").write_text("{}")
    (data / "mcp.json").write_text("[]")
    (data / "secrets.json").write_text(json.dumps({"hf_token": "hf_secret"}))
    return data


def test_backup_leaves_the_token_out_by_default(fake):
    seed_data()
    details = backup.create()
    root = Path(drive.BACKUP_DIR)
    assert (root / "skills" / "tone.md").read_text() == "be kind"
    assert not (root / "secrets.json").exists()
    assert not details["has_token"]


def test_token_is_backed_up_only_on_request(fake):
    seed_data()
    assert backup.create(include_token=True)["has_token"]
    assert (Path(drive.BACKUP_DIR) / "secrets.json").exists()
    backup.create()
    assert not (Path(drive.BACKUP_DIR) / "secrets.json").exists()


def test_restore_brings_everything_back(fake):
    data = seed_data()
    backup.create()
    shutil.rmtree(data / "skills")
    (data / "mcp.json").unlink()
    details = backup.restore()
    assert (data / "skills" / "tone.md").read_text() == "be kind"
    assert (data / "mcp.json").exists() and "skills" in details["items"]


def test_restore_without_a_backup_fails_clearly(fake):
    with pytest.raises(FileNotFoundError):
        backup.restore()


def test_failed_backup_keeps_the_previous_one(fake, monkeypatch):
    seed_data()
    backup.create()

    def broken(*args):
        raise OSError("drive full")

    monkeypatch.setattr(backup, "_copy_item", broken)
    with pytest.raises(OSError):
        backup.create()
    assert (Path(drive.BACKUP_DIR) / "skills" / "tone.md").exists()


def test_auto_backup_is_off_until_enabled_and_skips_when_unchanged(fake):
    data = seed_data()
    auto = backup.AutoBackup()
    assert not auto.run_once()
    backup.set_auto(True)
    assert auto.run_once()
    assert not auto.run_once()
    (data / "skills" / "new.md").write_text("more")
    assert auto.run_once()
    assert not (Path(drive.BACKUP_DIR) / "secrets.json").exists()


@pytest.fixture
def client(fake, tmp_path):
    from workstation.config import Settings
    from workstation.mcp.registry import McpRegistry
    from workstation.server import create_app

    return TestClient(create_app(Settings(model=None, tunnel=False), McpRegistry(tmp_path / "mcp.json")))


def test_api_status_and_browse(client):
    status = client.get("/api/drive/status").json()
    assert status["mounted"] and status["backup"] is None
    assert client.get("/api/drive/browse", params={"path": ".."}).status_code == 400
    assert client.get("/api/drive/browse", params={"path": "nope"}).status_code == 404


def test_api_backup_restore_and_transfer_shown_in_telemetry(client):
    seed_data()
    assert client.post("/api/drive/backup", json={}).status_code == 200
    assert client.get("/api/drive/status").json()["backup"]["items"]
    assert client.post("/api/drive/restore").status_code == 200

    source = Path(drive.DRIVE_ROOT) / "my.gguf"
    source.write_bytes(b"GGUF")
    assert client.post("/api/drive/transfer", json={"model_id": str(source)}).status_code == 200
    deadline = time.time() + 5
    while time.time() < deadline:
        job = client.get("/api/telemetry").json()["transfer"]
        if job["status"] == "done":
            break
        time.sleep(0.02)
    assert job["label"] == "my.gguf" and job["status"] == "done"


def test_api_refuses_restore_during_a_task(client):
    client.app.state.tasks.tasks["x"] = {"status": "running"}
    assert client.post("/api/drive/restore").status_code == 409


def test_config_data_dir_is_untouched(fake):
    assert config.DATA_DIR != backup.DATA_DIR

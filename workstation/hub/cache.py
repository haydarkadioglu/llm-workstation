import hashlib
import re
import shutil
from pathlib import Path
from typing import List, Optional
from urllib.parse import unquote

from huggingface_hub import HfApi, constants, hf_hub_download, scan_cache_dir, snapshot_download
from huggingface_hub.utils import filter_repo_objects

from ..config import DATA_DIR
from ..engine.gguf import parse_gguf_id, shard_names, split_gguf_id
from ..secrets import resolve_token
from . import drive
from .resumable import PARTIAL_SUFFIX, fetch_all, remote_size
from .source import is_url

GB = 1024 ** 3
GGUF_MAGIC = b"GGUF"
WEIGHT_PATTERNS = ("*.safetensors", "*.bin")
CONFIG_PATTERNS = ["*.json", "*.model", "*.txt", "*.py", "*.jinja", "*.tiktoken"]


def _cache_dirs() -> List[Optional[str]]:
    dirs: List[Optional[str]] = [None]
    if drive.is_drive_mounted() and Path(drive.DRIVE_LLM_DIR).is_dir():
        dirs.append(drive.DRIVE_LLM_DIR)
    return dirs


def _scan(cache: Optional[str]):
    try:
        return scan_cache_dir(cache).repos
    except Exception as err:
        print(f"[Cache] Could not scan {cache or 'default cache'}: {err}")
        return []


def gguf_files(model_id: str) -> tuple:
    repo_id, revision, filename = parse_gguf_id(model_id)
    return repo_id, revision, shard_names(filename)


def _url_file_prefix(url: str) -> str:
    return hashlib.sha1(url.split("?")[0].encode("utf-8")).hexdigest()[:8]


def _url_targets(url: str) -> List[tuple]:
    """(url, local path) for a linked GGUF and any other parts of it."""
    base, separator, query = url.partition("?")
    prefix = _url_file_prefix(url)
    targets = []
    for shard in shard_names(base):
        name = re.sub(r"[^\w.\-]", "_", unquote(shard.rsplit("/", 1)[-1]))
        targets.append((shard + separator + query, drive.url_models_dir() / f"{prefix}-{name}"))
    return targets


def download_url(url: str, report, note) -> tuple:
    """Fetch a GGUF from any site. Returns (first file path, total MB)."""
    drive.url_models_dir().mkdir(parents=True, exist_ok=True)
    files = [(link, dest, remote_size(link)) for link, dest in _url_targets(url)]
    if not all(size for _, _, size in files):
        note("This site does not report the file size, so pausing will start over.")
    fetch_all(files, None, report)

    for _, dest, _ in files:
        with open(dest, "rb") as handle:
            magic = handle.read(4)
        if magic != GGUF_MAGIC:
            dest.unlink()
            raise ValueError("That link did not return a GGUF file. It may need a login, or be a web page.")
    return str(files[0][1]), sum(dest.stat().st_size for _, dest, _ in files) / (1024 ** 2)


def _url_models() -> List[dict]:
    models = []
    for folder, location in ((DATA_DIR / "models", "local"), (Path(drive.DRIVE_GGUF_DIR), "drive")):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.gguf")):
            shards = shard_names(path.name)
            if len(shards) > 1 and path.name != shards[0]:
                continue
            size = sum((folder / shard).stat().st_size for shard in shards if (folder / shard).exists())
            name = re.sub(r"^[0-9a-f]{8}-", "", path.name)
            models.append({"id": str(path), "name": name, "size_gb": round(size / GB, 2), "kind": "gguf", "location": location})
    return models


def repo_files(repo_id: str, token: Optional[str]) -> List[str]:
    """The files download_repo would fetch."""
    files = HfApi().list_repo_files(repo_id, token=token)
    return list(filter_repo_objects(files, allow_patterns=CONFIG_PATTERNS + _weight_patterns(repo_id, token)))


def download_gguf(model_id: str, token: Optional[str]) -> tuple:
    """Fetch a GGUF (all shards if split). Returns (first file path, total MB)."""
    repo_id, revision, names = gguf_files(model_id)
    paths = [
        hf_hub_download(repo_id=repo_id, filename=name, revision=revision, cache_dir=drive.cache_dir(), token=token)
        for name in names
    ]
    return paths[0], sum(Path(p).stat().st_size for p in paths) / (1024 ** 2)


def _weight_patterns(repo_id: str, token: Optional[str]) -> List[str]:
    """Repos often ship both formats; fetching both doubles the download."""
    files = HfApi().list_repo_files(repo_id, token=token)
    has_safetensors = any(f.endswith(".safetensors") for f in files)
    return ["*.safetensors"] if has_safetensors else ["*.bin"]


def download_repo(repo_id: str, token: Optional[str]) -> str:
    kwargs = {}
    if drive.cache_dir():
        kwargs["max_workers"] = 1  # sequential, Drive's FUSE mount stalls otherwise
    return snapshot_download(
        repo_id=repo_id,
        cache_dir=drive.cache_dir(),
        allow_patterns=CONFIG_PATTERNS + _weight_patterns(repo_id, token),
        token=token,
        **kwargs,
    )


def list_cached() -> List[dict]:
    models = []
    for cache in _cache_dirs():
        location = "drive" if cache else "local"
        for repo in _scan(cache):
            files = [f for rev in repo.revisions for f in rev.files]
            ggufs = {f.file_name: f for f in files if f.file_name.endswith(".gguf")}
            for name, file in ggufs.items():
                shards = shard_names(name)
                if len(shards) > 1 and name != shards[0]:
                    continue
                size = sum(ggufs[s].size_on_disk for s in shards if s in ggufs)
                models.append({"id": f"{repo.repo_id}/{name}", "size_gb": round(size / GB, 2), "kind": "gguf", "location": location})
            diffusion = any(f.file_name == "model_index.json" for f in files)
            if diffusion or any(f.file_name == "config.json" for f in files) or not ggufs:
                kind = "diffusion" if diffusion else "repo"
                models.append({"id": repo.repo_id, "size_gb": round(repo.size_on_disk / GB, 2), "kind": kind, "location": location})
    return models + _url_models()


def _delete_gguf(repo, filename: str) -> None:
    targets = set(shard_names(filename))
    for rev in repo.revisions:
        for file in rev.files:
            if file.file_name in targets:
                Path(file.file_path).unlink(missing_ok=True)
                Path(file.blob_path).unlink(missing_ok=True)
    remaining = [
        f
        for rev in repo.revisions
        for f in rev.files
        if f.file_name.endswith(".gguf") and f.file_name not in targets
    ]
    if not remaining:
        shutil.rmtree(repo.repo_path)


def discard_partial(model_id: str) -> None:
    """Remove what a cancelled download left behind, keeping any finished files."""
    if is_url(model_id):
        for _, dest in _url_targets(model_id):
            dest.with_name(dest.name + PARTIAL_SUFFIX).unlink(missing_ok=True)
        return
    repo_id = parse_gguf_id(model_id)[0] if model_id.lower().endswith(".gguf") else model_id
    folder = "models--" + repo_id.replace("/", "--")
    for cache in _cache_dirs():
        repo_path = Path(cache or constants.HF_HUB_CACHE) / folder
        for pattern in ("*.incomplete", "*.partial"):
            for partial in repo_path.rglob(pattern):
                partial.unlink(missing_ok=True)
        blobs = repo_path / "blobs"
        if repo_path.is_dir() and not (blobs.is_dir() and any(blobs.iterdir())):
            shutil.rmtree(repo_path, ignore_errors=True)


def _delete_url_model(path: Path) -> None:
    for shard in shard_names(path.name):
        (path.parent / shard).unlink(missing_ok=True)


def delete_cached(model_id: str) -> bool:
    known = {model["id"] for model in _url_models()}
    if model_id in known:
        _delete_url_model(Path(model_id))
        return True
    is_gguf = model_id.lower().endswith(".gguf")
    try:
        repo_id, filename = split_gguf_id(model_id) if is_gguf else (model_id, None)
    except ValueError:
        return False
    for cache in _cache_dirs():
        for repo in _scan(cache):
            if repo.repo_id != repo_id:
                continue
            if filename:
                _delete_gguf(repo, Path(filename).name)
            else:
                shutil.rmtree(repo.repo_path)
            return True
    return False


def search_gguf(query: Optional[str]) -> List[dict]:
    models = HfApi().list_models(search=query or None, filter="gguf", limit=20, sort="downloads")
    return [{"repo_id": m.id, "downloads": m.downloads or 0, "likes": m.likes or 0} for m in models]


def list_gguf_files(repo_id: str) -> List[dict]:
    files = {}
    for item in HfApi().list_repo_tree(repo_id, recursive=True, token=resolve_token()):
        if item.path.endswith(".gguf"):
            files[item.path] = getattr(item, "size", 0) or 0

    results = []
    for path in sorted(files):
        shards = shard_names(path)
        if path != shards[0]:
            continue
        results.append({"filename": path, "size": sum(files.get(s, 0) for s in shards), "parts": len(shards)})
    return results

import shutil
from pathlib import Path
from typing import List, Optional

from huggingface_hub import HfApi, hf_hub_download, scan_cache_dir, snapshot_download

from ..engine.gguf import shard_names, split_gguf_id
from . import drive

GB = 1024 ** 3
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


def download_gguf(model_id: str, token: Optional[str]) -> tuple:
    """Fetch a GGUF (all shards if split). Returns (first file path, total MB)."""
    repo_id, filename = split_gguf_id(model_id)
    paths = [
        hf_hub_download(repo_id=repo_id, filename=name, cache_dir=drive.cache_dir(), token=token)
        for name in shard_names(filename)
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
            if any(f.file_name == "config.json" for f in files) or not ggufs:
                models.append({"id": repo.repo_id, "size_gb": round(repo.size_on_disk / GB, 2), "kind": "repo", "location": location})
    return models


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


def delete_cached(model_id: str) -> bool:
    is_gguf = model_id.lower().endswith(".gguf")
    repo_id, filename = split_gguf_id(model_id) if is_gguf else (model_id, None)
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
    for item in HfApi().list_repo_tree(repo_id, recursive=True):
        if item.path.endswith(".gguf"):
            files[item.path] = getattr(item, "size", 0) or 0

    results = []
    for path in sorted(files):
        shards = shard_names(path)
        if path != shards[0]:
            continue
        results.append({"filename": path, "size": sum(files.get(s, 0) for s in shards), "parts": len(shards)})
    return results

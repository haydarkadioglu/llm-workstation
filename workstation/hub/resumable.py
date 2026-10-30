"""Resumable downloads into the Hugging Face cache.

huggingface_hub 2 writes each download to a throwaway file, so a stopped download
starts over. Here a file is fetched with HTTP range requests into a partial file next
to the cache's blob and renamed into place. hf_hub_download then finds the blob and
only has to link it.
"""
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import urlparse

from huggingface_hub import constants, get_hf_file_metadata, hf_hub_url
from huggingface_hub.file_download import repo_folder_name

CHUNK = 1024 * 1024
TIMEOUT = 60
PARTIAL_SUFFIX = ".partial"
USER_AGENT = "llm-workstation"

Progress = Callable[[int], None]


def _headers(url: str, offset: int, token: Optional[str]) -> dict:
    headers = {"User-Agent": USER_AGENT}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    host = urlparse(url).hostname or ""
    if token and (host == "huggingface.co" or host.endswith(".huggingface.co")):
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _partial_size(dest: Path) -> int:
    partial = dest.with_name(dest.name + PARTIAL_SUFFIX)
    return partial.stat().st_size if partial.exists() else 0


def fetch(url: str, dest: Path, size: int, token: Optional[str], on_bytes: Progress) -> None:
    """Download `url` to `dest`, continuing from any partial file already there."""
    partial = dest.with_name(dest.name + PARTIAL_SUFFIX)
    offset = _partial_size(dest)
    if offset > size:
        partial.unlink()
        offset = 0

    if offset < size:
        request = urllib.request.Request(url, headers=_headers(url, offset, token))
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            if response.status != 206:  # the server ignored Range and sent the whole file
                offset = 0
            with open(partial, "ab" if offset else "wb") as out:
                while chunk := response.read(CHUNK):
                    out.write(chunk)
                    on_bytes(len(chunk))

    if partial.stat().st_size != size:
        raise IOError(f"Incomplete download of {dest.name}, try again to continue.")
    partial.replace(dest)


def prefetch(repo_id: str, filenames: List[str], token: Optional[str], cache_dir: Optional[str], report: Callable) -> None:
    """Put the given files into the cache as blobs, reporting overall progress."""
    root = Path(cache_dir or constants.HF_HUB_CACHE) / repo_folder_name(repo_id=repo_id, repo_type="model") / "blobs"
    root.mkdir(parents=True, exist_ok=True)

    files = []
    for name in filenames:
        meta = get_hf_file_metadata(hf_hub_url(repo_id, name), token=token)
        files.append((meta, root / meta.etag))

    total = sum(meta.size for meta, _ in files) or 1
    done = sum(meta.size if dest.exists() else _partial_size(dest) for meta, dest in files)
    fetched = 0
    started = time.time()

    def on_bytes(count: int) -> None:
        nonlocal done, fetched
        done += count
        fetched += count
        report(done / total, fetched / max(time.time() - started, 0.001))

    for meta, dest in files:
        if not dest.exists():
            fetch(meta.location, dest, meta.size, token, on_bytes)

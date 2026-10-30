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
from typing import Callable, List, Optional, Tuple
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


def remote_size(url: str) -> Optional[int]:
    """Size from a HEAD request, None when the server will not say."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            length = response.headers.get("Content-Length")
            return int(length) if length else None
    except (OSError, ValueError):
        return None


def fetch(url: str, dest: Path, size: Optional[int], token: Optional[str], on_bytes: Progress) -> None:
    """Download `url` to `dest`, continuing from any partial file already there.

    With an unknown size there is nothing to check a partial file against, so it starts over.
    """
    partial = dest.with_name(dest.name + PARTIAL_SUFFIX)
    offset = _partial_size(dest) if size else 0
    if size and offset > size:
        partial.unlink()
        offset = 0

    if not size or offset < size:
        request = urllib.request.Request(url, headers=_headers(url, offset, token))
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            if response.status != 206:  # the server ignored Range and sent the whole file
                offset = 0
            with open(partial, "ab" if offset else "wb") as out:
                while chunk := response.read(CHUNK):
                    out.write(chunk)
                    on_bytes(len(chunk))

    if size and partial.stat().st_size != size:
        raise IOError(f"Incomplete download of {dest.name}, try again to continue.")
    partial.replace(dest)


def fetch_all(files: List[Tuple[str, Path, Optional[int]]], token: Optional[str], report: Callable) -> None:
    """Fetch (url, dest, size) entries in turn, reporting progress across all of them."""
    total = sum(size or 0 for _, _, size in files)
    done = sum((size or 0) if dest.exists() else _partial_size(dest) for _, dest, size in files)
    fetched = 0
    started = time.time()

    def on_bytes(count: int) -> None:
        nonlocal done, fetched
        done += count
        fetched += count
        report(done / total if total else 0, fetched / max(time.time() - started, 0.001))

    for url, dest, size in files:
        if not dest.exists():
            fetch(url, dest, size, token, on_bytes)


def prefetch(
    repo_id: str,
    filenames: List[str],
    token: Optional[str],
    cache_dir: Optional[str],
    report: Callable,
    revision: Optional[str] = None,
) -> None:
    """Put the given files into the cache as blobs, reporting overall progress."""
    root = Path(cache_dir or constants.HF_HUB_CACHE) / repo_folder_name(repo_id=repo_id, repo_type="model") / "blobs"
    root.mkdir(parents=True, exist_ok=True)

    files = []
    for name in filenames:
        meta = get_hf_file_metadata(hf_hub_url(repo_id, name, revision=revision), token=token)
        files.append((meta.location, root / meta.etag, meta.size))
    fetch_all(files, token, report)

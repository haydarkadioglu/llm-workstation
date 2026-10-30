"""Runs one download in its own process, so it can be stopped at any moment.

hf_xet downloads outside Python and cannot be interrupted from a progress callback.
Killing this process works for every downloader. Messages to the parent are lines
starting with MARKER on stdout, since libraries print there too.
"""
import json
import os
import sys

from . import cache, drive
from .progress import format_speed, track_downloads
from .resumable import prefetch

MARKER = "@@"
FAST_DOWNLOAD = "LLM_WORKSTATION_FAST_DOWNLOAD"


def _send(**message) -> None:
    print(MARKER + json.dumps(message), flush=True)


class _Reporter:
    def __setattr__(self, name: str, value) -> None:
        if name == "status":
            pct, _, speed = value.partition("(")
            _send(pct=int("".join(c for c in pct if c.isdigit()) or 0), speed=speed.rstrip(")"))


def _report(fraction: float, rate: float) -> None:
    _send(pct=min(int(fraction * 100), 100), speed=format_speed(rate))


def _prefetch(request: dict) -> None:
    """Fetch resumably first, unless the user chose the faster hf_xet path."""
    kind, model_id, token = request["kind"], request["model_id"], request.get("token")
    if os.environ.get(FAST_DOWNLOAD):
        return
    if kind == "gguf":
        repo_id, revision, files = cache.gguf_files(model_id)
    else:
        repo_id, revision, files = model_id, None, cache.repo_files(model_id, token)
    prefetch(repo_id, files, token, drive.cache_dir(), _report, revision)


def _fetch(request: dict) -> dict:
    kind, model_id, token = request["kind"], request["model_id"], request.get("token")
    if kind == "url":
        path, size_mb = cache.download_url(model_id, _report, lambda text: _send(note=text))
        return {"path": path, "size_mb": size_mb}
    if kind == "gguf":
        _prefetch(request)
        path, size_mb = cache.download_gguf(model_id, token)
        return {"path": path, "size_mb": size_mb}
    if kind == "repo":
        _prefetch(request)
        return {"path": cache.download_repo(model_id, token)}
    import diffusers

    path = diffusers.DiffusionPipeline.download(model_id, token=token, cache_dir=drive.cache_dir())
    return {"path": str(path)}


def main() -> None:
    request = json.loads(sys.stdin.readline())
    if request.get("drive"):
        drive.set_drive_mode(True)
    try:
        with track_downloads(_Reporter()):
            _send(result=_fetch(request))
    except Exception as err:
        _send(error=str(err))


if __name__ == "__main__":
    main()

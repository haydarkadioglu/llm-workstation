from contextlib import contextmanager

import tqdm


def _format_speed(rate) -> str:
    if rate is None:
        return ""
    for limit, unit in ((1024 ** 2, "MB/s"), (1024, "KB/s")):
        if rate > limit:
            return f"{rate / limit:.1f} {unit}"
    return f"{rate:.1f} B/s"


@contextmanager
def track_downloads(state):
    """Mirror tqdm bars into `state`.

    huggingface_hub, hf_xet and diffusers each draw their own bars and only
    snapshot_download accepts a tqdm class (per file, not per byte), so patching
    tqdm is the one hook that reports byte progress for all of them.
    """
    original_init = tqdm.tqdm.__init__
    original_update = tqdm.tqdm.update

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        state.loading_progress = 0
        state.loading_speed = ""

    def patched_update(self, n=1):
        result = original_update(self, n)
        if self.total:
            pct = int(self.n / self.total * 100)
            speed = _format_speed(self.format_dict.get("rate"))
            state.loading_progress = pct
            state.loading_speed = speed
            state.status = f"Downloading: {pct}%" + (f" ({speed})" if speed else "")
        return result

    tqdm.tqdm.__init__ = patched_init
    tqdm.tqdm.update = patched_update
    try:
        yield
    finally:
        tqdm.tqdm.__init__ = original_init
        tqdm.tqdm.update = original_update

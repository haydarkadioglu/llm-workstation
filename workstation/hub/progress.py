import time
from contextlib import contextmanager

import tqdm


def format_speed(rate: float) -> str:
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
        self.tracked_done = kwargs.get("initial", 0) or 0
        self.tracked_start = time.time()

    def patched_update(self, n=1):
        result = original_update(self, n)
        # tqdm turns itself off without a terminal and then stops counting, so count here.
        self.tracked_done += n or 0
        if self.total:
            pct = min(int(self.tracked_done / self.total * 100), 100)
            speed = format_speed(self.tracked_done / max(time.time() - self.tracked_start, 0.001))
            state.status = f"Downloading: {pct}%" + (f" ({speed})" if speed else "")
        return result

    tqdm.tqdm.__init__ = patched_init
    tqdm.tqdm.update = patched_update
    try:
        yield
    finally:
        tqdm.tqdm.__init__ = original_init
        tqdm.tqdm.update = original_update

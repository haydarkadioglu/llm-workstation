import gc
import os
import threading
from typing import Iterator, List, Optional, Union

from ..config import DATA_DIR, read_json, write_json
from ..hub import cache
from ..hub.download import DownloadStopped, run_download
from ..telemetry import free_vram_mb
from .base import ChatBackend, GenerationParams, Message, Usage

SETTINGS_FILE = DATA_DIR / "model_settings.json"


def saved_model_settings(model_id: str) -> dict:
    return read_json(SETTINGS_FILE, {}).get(model_id, {})


def _save_model_settings(model_id: str, options: dict) -> None:
    settings = read_json(SETTINGS_FILE, {})
    settings[model_id] = options
    write_json(SETTINGS_FILE, settings)


class ModelManager:
    """Owns the single loaded model. One operation runs on it at a time."""

    def __init__(self):
        self.lock = threading.Lock()
        self.backend: Optional[ChatBackend] = None
        self.pipeline = None
        self.pipeline_kind: Optional[str] = None
        self.model_id: Optional[str] = None
        self.status = "No model loaded"
        self.loading_progress = 0
        self.loading_speed = ""
        self.error_message: Optional[str] = None
        self.tokens_per_sec = 0.0
        self.last_usage: Optional[Usage] = None
        self._active_cancel: Optional[threading.Event] = None
        self.load_state = "idle"  # idle | preparing | downloading | loading | paused
        self.loading_model: Optional[str] = None
        self.stop_request: Optional[str] = None
        self.paused_load: Optional[tuple] = None

    def _release(self) -> None:
        if self.backend:
            self.backend.close()
        self.backend = None
        self.pipeline = None
        self.pipeline_kind = None
        self.model_id = None
        self.last_usage = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def _interrupt(self) -> None:
        if self._active_cancel:
            self._active_cancel.set()

    def unload(self) -> None:
        self._interrupt()
        self.paused_load = None
        self.load_state = "idle"
        with self.lock:
            self._release()
            self.status = "No model loaded"
            self.loading_progress = 0
            self.loading_speed = ""
            self.error_message = None

    def load(self, model_id: str, token: Optional[str] = None, kind: str = "text", options: Optional[dict] = None) -> bool:
        options = {**saved_model_settings(model_id), **(options or {})}
        token = (token or "").strip() or None
        self.paused_load = None
        self._interrupt()
        with self.lock:
            self._release()
            self.stop_request = None
            self.load_state = "preparing"
            self.loading_model = model_id
            self.status = f"Preparing '{model_id}'..."
            self.loading_progress = 0
            self.loading_speed = ""
            self.error_message = None
            try:
                if kind in ("image", "video"):
                    self._load_pipeline(model_id, kind, token)
                else:
                    self.backend = self._load_chat(model_id, token, options)
                self.model_id = model_id
                self.status = "Ready"
                self.loading_progress = 100
                self.loading_speed = ""
                self.load_state = "idle"
                if options:
                    _save_model_settings(model_id, options)
                print(f"[Model] '{model_id}' loaded.")
                return True
            except DownloadStopped as stop:
                self._after_stop(stop.reason, (model_id, token, kind, options))
                return False
            except Exception as err:
                self._release()
                self.load_state = "idle"
                self.status = "Error loading model"
                self.error_message = str(err)
                print(f"[Model] Load failed: {err}")
                return False

    def _after_stop(self, reason: str, request: tuple) -> None:
        self.stop_request = None
        self.loading_speed = ""
        if reason == "pause":
            self.paused_load = request
            self.load_state = "paused"
            self.status = f"Paused at {self.loading_progress}%"
        else:
            self._finish_cancel(request[0])

    def _finish_cancel(self, model_id: str) -> None:
        try:
            cache.discard_partial(model_id)
        except OSError as err:
            print(f"[Model] Could not remove partial files: {err}")
        self.load_state = "idle"
        self.loading_model = None
        self.loading_progress = 0
        self.status = "No model loaded"

    def pause_load(self) -> bool:
        if self.load_state != "downloading":
            return False
        self.stop_request = "pause"
        return True

    def resume_load(self) -> bool:
        if not self.paused_load:
            return False
        request, self.paused_load = self.paused_load, None
        threading.Thread(target=self.load, args=request, daemon=True).start()
        return True

    def cancel_load(self) -> bool:
        if self.load_state == "downloading":
            self.stop_request = "cancel"
            return True
        if self.load_state == "paused":
            model_id = self.paused_load[0]
            self.paused_load = None
            self._finish_cancel(model_id)
            return True
        return False

    def _download(self, kind: str, model_id: str, token: Optional[str]) -> dict:
        self.load_state = "downloading"
        result = run_download(kind, model_id, token, self)
        self.load_state = "loading"
        return result

    def _load_pipeline(self, model_id: str, kind: str, token: Optional[str]) -> None:
        from .diffusion import load_pipeline

        path = model_id if os.path.isdir(model_id) else self._download("pipeline", model_id, token)["path"]
        self.status = "Loading pipeline into memory..."
        self.pipeline = load_pipeline(path, kind, token)
        self.pipeline_kind = kind

    def _load_chat(self, model_id: str, token: Optional[str], options: dict) -> ChatBackend:
        n_ctx = options.get("n_ctx")
        n_gpu_layers = options.get("n_gpu_layers", -1)
        lower = model_id.lower()

        if lower.endswith(".gguf"):
            from .gguf import GgufBackend

            if os.path.isfile(model_id):
                path, size_mb = model_id, os.path.getsize(model_id) / (1024 ** 2)
            else:
                result = self._download("gguf", model_id, token)
                path, size_mb = result["path"], result["size_mb"]
            self.status = "Loading GGUF into memory..."
            return GgufBackend(path, n_ctx, n_gpu_layers, free_vram_mb(), size_mb)

        if "-gguf" in lower:
            raise ValueError("GGUF repos hold several files. Pick one: org/repo/model-Q4_K_M.gguf")

        from .hf import HfBackend

        path = model_id if os.path.isdir(model_id) else self._download("repo", model_id, token)["path"]
        self.status = "Loading weights into memory..."
        return HfBackend(path, model_id)

    def stream(
        self,
        messages: List[Message],
        tools: Optional[List[dict]],
        params: GenerationParams,
        cancel: threading.Event,
    ) -> Iterator[Union[str, Usage]]:
        with self.lock:
            if not self.backend:
                raise RuntimeError("No chat model is loaded.")
            self._active_cancel = cancel
            try:
                for item in self.backend.stream(messages, tools, params, cancel):
                    if isinstance(item, Usage):
                        self.tokens_per_sec = item.tokens_per_sec
                        self.last_usage = item
                    yield item
            finally:
                self._active_cancel = None

    def count_tokens(self, messages: List[Message], tools: Optional[List[dict]] = None) -> int:
        with self.lock:
            if not self.backend:
                raise RuntimeError("No chat model is loaded.")
            return self.backend.count_tokens(messages, tools)

    def require_pipeline(self, kind: str):
        if not self.pipeline or self.pipeline_kind != kind:
            raise ValueError(f"No {kind} model is loaded. Load a {kind} model first.")
        return self.pipeline

    def generate_image(self, **kwargs):
        from .diffusion import generate_image

        with self.lock:
            return generate_image(self.require_pipeline("image"), **kwargs)

    def generate_video(self, **kwargs) -> str:
        from .video import generate_video

        with self.lock:
            return generate_video(self.require_pipeline("video"), **kwargs)

    def snapshot(self) -> dict:
        caps = self.backend.capabilities if self.backend else None
        usage = self.last_usage
        return {
            "model_status": self.status,
            "current_model": self.model_id,
            "model_kind": self.pipeline_kind or ("text" if self.backend else None),
            "load_state": self.load_state,
            "loading_model": self.loading_model,
            "loading_progress": self.loading_progress,
            "loading_speed": self.loading_speed,
            "error_message": self.error_message,
            "tokens_per_sec": self.tokens_per_sec,
            "supports_tools": bool(caps and caps.tools),
            "is_vision": bool(caps and caps.vision),
            "context_length": caps.context_length if caps else 0,
            "context_used": (usage.prompt_tokens + usage.completion_tokens) if usage else 0,
        }

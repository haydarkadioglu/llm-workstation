import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Union

Message = Dict[str, Any]


@dataclass
class GenerationParams:
    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 40
    min_p: float = 0.05
    repeat_penalty: float = 1.1
    max_tokens: int = 1024
    stop: List[str] = field(default_factory=list)
    seed: Optional[int] = None


@dataclass
class Capabilities:
    tools: bool = False
    vision: bool = False
    context_length: int = 4096


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = "stop"
    tokens_per_sec: float = 0.0


class ChatBackend(ABC):
    capabilities: Capabilities

    @abstractmethod
    def stream(
        self,
        messages: List[Message],
        tools: Optional[List[dict]],
        params: GenerationParams,
        cancel: threading.Event,
    ) -> Iterator[Union[str, Usage]]:
        """Yield text pieces, then exactly one Usage."""

    @abstractmethod
    def count_tokens(self, messages: List[Message], tools: Optional[List[dict]] = None) -> int:
        ...

    def close(self) -> None:
        pass


class StopFilter:
    """Cuts streamed text at the first stop string, holding back partial matches."""

    def __init__(self, stops: List[str]):
        self.stops = [s for s in stops if s]
        self.hold = max((len(s) for s in self.stops), default=1) - 1
        self.buffer = ""
        self.stopped = False

    def feed(self, text: str) -> str:
        if self.stopped:
            return ""
        if not self.stops:
            return text
        self.buffer += text
        hits = [i for i in (self.buffer.find(s) for s in self.stops) if i != -1]
        if hits:
            self.stopped = True
            out, self.buffer = self.buffer[: min(hits)], ""
            return out
        cut = max(len(self.buffer) - self.hold, 0)
        out, self.buffer = self.buffer[:cut], self.buffer[cut:]
        return out

    def flush(self) -> str:
        out, self.buffer = self.buffer, ""
        return "" if self.stopped else out

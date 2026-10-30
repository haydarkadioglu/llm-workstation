import re
import threading
import time
from typing import Dict, Iterator, List, Optional, Tuple, Union

from .base import Capabilities, ChatBackend, GenerationParams, Message, Usage
from .template import CHATML_TEMPLATE, ChatRenderer, normalize_messages, supports_tools

MIN_CTX = 2048
SHARD_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$")


def split_gguf_id(model_id: str) -> Tuple[str, str]:
    """'org/repo/sub/file.gguf' -> ('org/repo', 'sub/file.gguf')."""
    parts = model_id.split("/")
    if len(parts) < 3:
        raise ValueError(f"GGUF id must look like org/repo/file.gguf, got '{model_id}'")
    return "/".join(parts[:2]), "/".join(parts[2:])


def shard_names(filename: str) -> List[str]:
    match = SHARD_RE.search(filename)
    if not match:
        return [filename]
    total = int(match.group(2))
    prefix = filename[: match.start()]
    return [f"{prefix}-{i:05d}-of-{total:05d}.gguf" for i in range(1, total + 1)]


def default_n_ctx(train_ctx: int, free_vram_mb: Optional[float], file_size_mb: float) -> int:
    if free_vram_mb is None:
        budget = 8192
    else:
        spare = free_vram_mb - file_size_mb
        budget = 32768 if spare > 8000 else 16384 if spare > 3000 else 8192
    return max(MIN_CTX, min(train_ctx or budget, budget))


def _templates_from_metadata(metadata: Dict[str, str]) -> Dict[str, str]:
    prefix = "tokenizer.chat_template"
    templates = {}
    for key, value in metadata.items():
        if key == prefix:
            templates["default"] = value
        elif key.startswith(prefix + "."):
            templates[key[len(prefix) + 1 :]] = value
    return templates or {"default": CHATML_TEMPLATE}


class GgufBackend(ChatBackend):
    def __init__(self, path: str, n_ctx: Optional[int], n_gpu_layers: int, free_vram_mb: Optional[float], file_size_mb: float):
        import llama_cpp

        probe = llama_cpp.Llama(model_path=path, vocab_only=True, verbose=False)
        metadata = dict(probe.metadata)
        del probe

        arch = metadata.get("general.architecture", "")
        train_ctx = int(metadata.get(f"{arch}.context_length", 0) or 0)
        wanted = n_ctx or default_n_ctx(train_ctx, free_vram_mb, file_size_mb)

        self.llm = None
        last_error = None
        for size in (wanted, wanted // 2, wanted // 4):
            try:
                self.llm = llama_cpp.Llama(
                    model_path=path, n_gpu_layers=n_gpu_layers, n_ctx=size, n_batch=512, verbose=False
                )
                break
            except ValueError as err:
                print(f"[GGUF] Could not create a {size} token context: {err}")
                last_error = err
        if self.llm is None:
            raise RuntimeError(f"Could not allocate a context for this model: {last_error}")

        vocab = self.llm
        self.eos_token = vocab.detokenize([vocab.token_eos()], special=True).decode("utf-8", "ignore")
        bos = vocab.detokenize([vocab.token_bos()], special=True).decode("utf-8", "ignore") if vocab.token_bos() >= 0 else ""
        templates = _templates_from_metadata(metadata)
        self.renderer = ChatRenderer(templates, bos_token=bos, eos_token=self.eos_token)
        self.capabilities = Capabilities(
            tools=any(supports_tools(t) for t in templates.values()),
            context_length=self.llm.n_ctx(),
        )

    def _prompt_tokens(self, messages: List[Message], tools: Optional[List[dict]]) -> Tuple[str, List[int]]:
        prompt = self.renderer.render(normalize_messages(messages), tools)
        tokens = self.llm.tokenize(prompt.encode("utf-8"), add_bos=False, special=True)
        return prompt, tokens

    def count_tokens(self, messages: List[Message], tools: Optional[List[dict]] = None) -> int:
        return len(self._prompt_tokens(messages, tools)[1])

    def stream(
        self,
        messages: List[Message],
        tools: Optional[List[dict]],
        params: GenerationParams,
        cancel: threading.Event,
    ) -> Iterator[Union[str, Usage]]:
        prompt, tokens = self._prompt_tokens(messages, tools)
        if prompt.rstrip().endswith("<think>"):
            yield "<think>"

        room = max(self.llm.n_ctx() - len(tokens) - 1, 1)
        stops = list(params.stop) + ([self.eos_token] if self.eos_token else [])
        if params.seed is not None:
            self.llm.set_seed(params.seed)

        started = time.time()
        count = 0
        finish = "stop"
        generator = self.llm.create_completion(
            prompt=tokens,
            max_tokens=min(params.max_tokens, room),
            temperature=params.temperature,
            top_p=params.top_p,
            top_k=params.top_k,
            min_p=params.min_p,
            repeat_penalty=params.repeat_penalty,
            stop=stops,
            stream=True,
        )
        try:
            for chunk in generator:
                if cancel.is_set():
                    finish = "cancelled"
                    break
                choice = chunk["choices"][0]
                if choice.get("text"):
                    count += 1
                    yield choice["text"]
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
        finally:
            generator.close()

        elapsed = time.time() - started
        yield Usage(len(tokens), count, finish, round(count / elapsed, 2) if elapsed > 0 else 0.0)

    def close(self) -> None:
        if self.llm is not None:
            self.llm.close()
            self.llm = None

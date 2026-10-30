import base64
import threading
import time
from io import BytesIO
from typing import Iterator, List, Optional, Union

import torch
from PIL import Image
from transformers import (
    AutoConfig,
    AutoModelForCausalLM,
    AutoProcessor,
    AutoTokenizer,
    BitsAndBytesConfig,
    StoppingCriteria,
    StoppingCriteriaList,
    TextIteratorStreamer,
)

from .base import Capabilities, ChatBackend, GenerationParams, Message, StopFilter, Usage
from .template import CHATML_TEMPLATE, merge_system_into_user, normalize_messages, supports_tools

VISION_TYPES = ("vision", "vl", "mllama", "llava", "paligemma", "internvl")


def decode_image(data_url: str) -> Image.Image:
    payload = data_url.split(",", 1)[-1]
    return Image.open(BytesIO(base64.b64decode(payload))).convert("RGB")


class _Cancelled(StoppingCriteria):
    def __init__(self, cancel: threading.Event):
        self.cancel = cancel

    def __call__(self, input_ids, scores, **kwargs) -> bool:
        return self.cancel.is_set()


def _load_weights(path: str, config: AutoConfig):
    common = dict(trust_remote_code=True)
    if not torch.cuda.is_available():
        print("[HF] No GPU found, loading on CPU.")
        return AutoModelForCausalLM.from_pretrained(path, device_map="cpu", **common)

    if "quantization_config" in config.to_dict():
        return AutoModelForCausalLM.from_pretrained(path, device_map="auto", **common)

    try:
        quant = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
        return AutoModelForCausalLM.from_pretrained(path, quantization_config=quant, device_map="auto", **common)
    except Exception as err:
        print(f"[HF] 4-bit load failed ({err}), falling back to float16.")
        return AutoModelForCausalLM.from_pretrained(path, device_map="auto", dtype=torch.float16, **common)


class HfBackend(ChatBackend):
    def __init__(self, path: str, model_id: str):
        config = AutoConfig.from_pretrained(path, trust_remote_code=True)
        model_type = (getattr(config, "model_type", "") or "").lower()
        vision = any(t in model_type for t in VISION_TYPES) or any(
            t in model_id.lower() for t in ("vision", "-vl-", "llava", "paligemma")
        )

        self.processor = None
        if vision:
            try:
                self.processor = AutoProcessor.from_pretrained(path, trust_remote_code=True)
            except Exception as err:
                print(f"[HF] AutoProcessor failed ({err}), using plain tokenizer.")
        self.tokenizer = (
            self.processor.tokenizer if self.processor else AutoTokenizer.from_pretrained(path, trust_remote_code=True)
        )
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        if not self.tokenizer.chat_template:
            self.tokenizer.chat_template = CHATML_TEMPLATE

        self.model = _load_weights(path, config)
        template = self.tokenizer.chat_template
        if isinstance(template, dict):
            template = "".join(template.values())
        elif isinstance(template, list):
            template = "".join(t.get("template", "") for t in template)
        self.capabilities = Capabilities(
            tools=supports_tools(template),
            vision=bool(self.processor),
            context_length=int(getattr(config, "max_position_embeddings", 0) or getattr(self.tokenizer, "model_max_length", 4096)),
        )
        if self.capabilities.context_length > 1_000_000:
            self.capabilities.context_length = 32768

    def _render(self, messages: List[Message], tools: Optional[List[dict]]) -> str:
        args = dict(tokenize=False, add_generation_prompt=True, tools=tools or None)
        try:
            return self.tokenizer.apply_chat_template(messages, **args)
        except Exception:
            return self.tokenizer.apply_chat_template(merge_system_into_user(messages), **args)

    def count_tokens(self, messages: List[Message], tools: Optional[List[dict]] = None) -> int:
        return len(self.tokenizer(self._render(normalize_messages(messages), tools)).input_ids)

    def _build_inputs(self, messages: List[Message], tools: Optional[List[dict]]):
        images = []
        if self.processor:
            rich = normalize_messages(messages, keep_images=True)
            for msg in rich:
                if not isinstance(msg["content"], list):
                    continue
                parts = []
                for part in msg["content"]:
                    if part.get("type") == "image_url":
                        images.append(decode_image(part["image_url"]["url"]))
                        parts.append({"type": "image"})
                    else:
                        parts.append(part)
                msg["content"] = parts
            if images:
                prompt = self.processor.apply_chat_template(rich, tokenize=False, add_generation_prompt=True)
                inputs = self.processor(text=[prompt], images=images, padding=True, return_tensors="pt")
                return prompt, inputs.to(self.model.device)

        prompt = self._render(normalize_messages(messages), tools)
        return prompt, self.tokenizer(prompt, return_tensors="pt").to(self.model.device)

    def stream(
        self,
        messages: List[Message],
        tools: Optional[List[dict]],
        params: GenerationParams,
        cancel: threading.Event,
    ) -> Iterator[Union[str, Usage]]:
        prompt, inputs = self._build_inputs(messages, tools)
        if prompt.rstrip().endswith("<think>"):
            yield "<think>"
        prompt_tokens = int(inputs["input_ids"].shape[1])
        if params.seed is not None:
            torch.manual_seed(params.seed)

        streamer = TextIteratorStreamer(self.tokenizer, skip_prompt=True, skip_special_tokens=True)
        sample = params.temperature > 0.0
        kwargs = dict(
            **inputs,
            streamer=streamer,
            max_new_tokens=params.max_tokens,
            do_sample=sample,
            repetition_penalty=params.repeat_penalty,
            stopping_criteria=StoppingCriteriaList([_Cancelled(cancel)]),
        )
        if sample:
            kwargs.update(temperature=params.temperature, top_p=params.top_p, top_k=params.top_k, min_p=params.min_p)

        errors: List[Exception] = []

        def run():
            try:
                self.model.generate(**kwargs)
            except Exception as err:
                errors.append(err)
                streamer.end()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        started = time.time()
        count = 0
        stops = StopFilter(params.stop)
        for piece in streamer:
            text = stops.feed(piece)
            if piece:
                count += 1
            if text:
                yield text
            if stops.stopped:
                cancel.set()
        tail = stops.flush()
        if tail:
            yield tail
        thread.join()
        if errors:
            raise errors[0]

        elapsed = time.time() - started
        if stops.stopped:
            finish = "stop"
        elif cancel.is_set():
            finish = "cancelled"
        else:
            finish = "length" if count >= params.max_tokens else "stop"
        yield Usage(prompt_tokens, count, finish, round(count / elapsed, 2) if elapsed > 0 else 0.0)

    def close(self) -> None:
        self.model = None
        self.tokenizer = None
        self.processor = None

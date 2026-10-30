"""Understands whatever gets pasted into a model box: ids, paths and links."""
from dataclasses import dataclass
from typing import List
from urllib.parse import unquote, urlparse

HF_HOSTS = {"huggingface.co", "www.huggingface.co", "hf.co"}
HF_FILE_VIEWS = {"blob", "resolve", "raw"}
HF_NOT_MODELS = {"datasets", "spaces", "docs", "settings", "organizations"}


@dataclass
class ModelSource:
    model_id: str
    kind: str  # gguf | repo | url
    needs_file_choice: bool = False


def is_url(text: str) -> bool:
    return text.lower().startswith(("http://", "https://"))


def _is_gguf_repo(repo: str) -> bool:
    return repo.lower().endswith("gguf")


def _from_hugging_face(parts: List[str]) -> ModelSource:
    if len(parts) < 2 or parts[0] in HF_NOT_MODELS:
        raise ValueError("This Hugging Face link is not a model. Link to a model page or a .gguf file.")
    repo = "/".join(parts[:2])
    rest = parts[2:]
    if len(rest) < 3 or rest[0] not in HF_FILE_VIEWS:
        return ModelSource(repo, "repo", _is_gguf_repo(repo))

    revision, filename = rest[1], "/".join(rest[2:])
    if not filename.lower().endswith(".gguf"):
        raise ValueError(f"Link to a .gguf file or to the model page, not to {filename}.")
    pinned = "" if revision == "main" else f"@{revision}"
    return ModelSource(f"{repo}{pinned}/{filename}", "gguf")


def parse_model_source(text: str) -> ModelSource:
    text = text.strip().strip("\"'")
    if not text:
        raise ValueError("Enter a model name or link.")
    if not is_url(text):
        return ModelSource(text, "gguf" if text.lower().endswith(".gguf") else "repo")

    parsed = urlparse(text)
    if (parsed.hostname or "").lower() in HF_HOSTS:
        return _from_hugging_face([unquote(p) for p in parsed.path.split("/") if p])
    if not unquote(parsed.path).lower().endswith(".gguf"):
        raise ValueError("Only direct links to .gguf files can be loaded from other sites.")
    return ModelSource(text, "url")

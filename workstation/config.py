import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

DEFAULT_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
DATA_DIR = Path(os.environ.get("LLM_WORKSTATION_HOME", "~/.llm-workstation")).expanduser()


@dataclass
class Settings:
    host: str = "0.0.0.0"
    port: int = 8000
    model: Optional[str] = DEFAULT_MODEL
    tunnel: bool = True


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)

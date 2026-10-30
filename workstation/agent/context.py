from typing import Callable, List, Optional, Tuple

RESPONSE_RESERVE = 0.25
MIN_RESPONSE_TOKENS = 256
TOOL_RESULT_SHARE = 0.25
CHARS_PER_TOKEN = 3


def clip_tool_result(text: str, context_length: int) -> str:
    """Keep head and tail of oversized results so one tool cannot flood the context."""
    limit = int(context_length * TOOL_RESULT_SHARE * CHARS_PER_TOKEN)
    if len(text) <= limit:
        return text
    head = int(limit * 0.8)
    tail = limit - head
    return f"{text[:head]}\n...[{len(text) - limit} characters omitted]...\n{text[-tail:]}"


def _drop_oldest(messages: List[dict]) -> bool:
    """Remove the oldest turn, keeping tool results with their call."""
    start = 1 if messages and messages[0]["role"] == "system" else 0
    if len(messages) - start <= 1:
        return False
    if not any(m["role"] == "user" for m in messages[start + 1 :]):
        return _drop_oldest_step(messages, start)
    del messages[start]
    while len(messages) - start > 1 and messages[start]["role"] != "user":
        del messages[start]
    return True


def _drop_oldest_step(messages: List[dict], start: int) -> bool:
    """One user turn with a long tool run: keep it, drop the run's oldest step."""
    end = start + 2
    while end < len(messages) and messages[end]["role"] == "tool":
        end += 1
    if end >= len(messages):
        return False  # only the latest step is left
    del messages[start + 1 : end]
    return True


def fit_context(
    messages: List[dict],
    tools: Optional[List[dict]],
    count_tokens: Callable[[List[dict], Optional[List[dict]]], int],
    context_length: int,
    max_tokens: int,
) -> Tuple[List[dict], int, int]:
    """Drop old turns until the prompt fits. Returns (messages, prompt tokens, turns dropped)."""
    reserve = max(MIN_RESPONSE_TOKENS, min(max_tokens, int(context_length * RESPONSE_RESERVE)))
    budget = context_length - reserve
    trimmed = list(messages)
    dropped = 0
    used = count_tokens(trimmed, tools)
    while used > budget and _drop_oldest(trimmed):
        dropped += 1
        used = count_tokens(trimmed, tools)
    return trimmed, used, dropped

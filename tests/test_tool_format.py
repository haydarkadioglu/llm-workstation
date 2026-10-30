import pytest

from workstation.agent.tool_format import StreamParser, legacy_messages, parse_calls

TOOLS = [{"type": "function", "function": {"name": "get_price", "description": "", "parameters": {}}}]


def run(text, tools=TOOLS, chunk=3):
    parser = StreamParser(tools)
    out = []
    for i in range(0, len(text), chunk):
        out += parser.feed(text[i : i + chunk])
    out += parser.finish()
    joined = {"text": "", "thinking": ""}
    for kind, piece in out:
        joined[kind] += piece
    return joined, parser


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 1000])
def test_hermes_call_split_across_tokens(chunk):
    joined, parser = run('Sure.\n<tool_call>\n{"name": "get_price", "arguments": {"sku": "A"}}\n</tool_call>', chunk=chunk)
    assert joined["text"].strip() == "Sure."
    assert [(c.name, c.arguments) for c in parser.calls] == [("get_price", {"sku": "A"})]


def test_several_calls_in_one_response():
    text = '<tool_call>{"name":"get_price","arguments":{"sku":"A"}}</tool_call><tool_call>{"name":"get_price","arguments":{"sku":"B"}}</tool_call>'
    _, parser = run(text)
    assert [c.arguments["sku"] for c in parser.calls] == ["A", "B"]


def test_unclosed_tool_call_is_still_collected():
    _, parser = run('<tool_call>{"name":"get_price","arguments":{"sku":"A"}}')
    assert len(parser.calls) == 1


def test_mistral_prefix_with_list():
    _, parser = run('[TOOL_CALLS][{"name":"get_price","arguments":{"sku":"A"}}]')
    assert parser.calls[0].name == "get_price"


def test_llama_python_tag_with_parameters_key():
    _, parser = run('<|python_tag|>{"name":"get_price","parameters":{"sku":"A"}}')
    assert parser.calls[0].arguments == {"sku": "A"}


def test_fenced_json_call():
    joined, parser = run('```json\n{"name": "get_price", "arguments": {"sku": "A"}}\n```')
    assert parser.calls[0].name == "get_price"
    assert joined["text"] == ""


def test_bare_json_that_is_not_a_tool_stays_text():
    joined, parser = run('{"city": "Izmir"}')
    assert not parser.calls
    assert joined["text"] == '{"city": "Izmir"}'


def test_doubled_opening_brace_from_qwen_is_repaired():
    text = '<tool_call>\n{{"name": "get_price", "arguments": {"sku": "A"}}\n</tool_call>'
    _, parser = run(text)
    assert parser.calls[0].arguments == {"sku": "A"}
    assert not parser.errors


def test_extra_closing_braces_are_repaired():
    calls, error = parse_calls('{{"name": "get_price", "arguments": {"sku": "A"}}}}')
    assert error is None
    assert calls[0].arguments == {"sku": "A"}


def test_missing_closing_brace_is_repaired():
    calls, error = parse_calls('{"name": "get_price", "arguments": {"sku": "A"}')
    assert calls[0].name == "get_price" and error is None


def test_braces_inside_strings_do_not_break_valid_json():
    calls, error = parse_calls('{"name": "get_price", "arguments": {"sku": "{A"}}')
    assert error is None
    assert calls[0].arguments == {"sku": "{A"}


def test_call_fenced_inside_prose_is_rescued():
    joined, parser = run('I will look it up:\n```json\n{"name": "get_price", "arguments": {"sku": "A"}}\n```\nOne moment.')
    assert parser.calls[0].arguments == {"sku": "A"}
    assert "look it up" in joined["text"]


def test_unrelated_fenced_json_in_prose_is_not_a_call():
    _, parser = run('Example:\n```json\n{"name": "Ada", "age": 3}\n```')
    assert not parser.calls


def test_string_arguments_are_decoded():
    calls, _ = parse_calls('{"name": "get_price", "arguments": "{\\"sku\\": \\"A\\"}"}')
    assert calls[0].arguments == {"sku": "A"}


def test_openai_shaped_call():
    calls, _ = parse_calls('{"function": {"name": "get_price", "arguments": {"sku": "A"}}}')
    assert calls[0].name == "get_price"


def test_broken_json_reports_error():
    _, parser = run('<tool_call>{"name": "get_price", "arguments": {"sku": <sku>}}</tool_call>')
    assert not parser.calls
    assert parser.errors


def test_markers_ignored_without_tools():
    joined, parser = run('<tool_call>{"name":"x"}</tool_call>', tools=None)
    assert not parser.calls
    assert "<tool_call>" in joined["text"]


@pytest.mark.parametrize("chunk", [1, 4, 1000])
def test_thinking_is_separated(chunk):
    joined, parser = run("<think>let me see</think>The answer is 4.", chunk=chunk)
    assert joined["thinking"] == "let me see"
    assert joined["text"] == "The answer is 4."


def test_call_after_thinking_as_bare_json():
    joined, parser = run('<think>need price</think>\n{"name": "get_price", "arguments": {"sku": "A"}}')
    assert joined["thinking"] == "need price"
    assert parser.calls[0].name == "get_price"


def test_unfinished_thinking_is_flushed():
    joined, _ = run("<think>still going")
    assert joined["thinking"] == "still going"


def test_angle_bracket_text_is_not_swallowed():
    joined, _ = run("if a < b and c <d then")
    assert joined["text"] == "if a < b and c <d then"


def test_legacy_messages_rewrite_tool_turns():
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_price", "arguments": "{}"}}]},
        {"role": "tool", "name": "get_price", "tool_call_id": "1", "content": "5"},
    ]
    out = legacy_messages(history, TOOLS)
    assert out[0]["role"] == "system" and "get_price" in out[0]["content"]
    assert [m["role"] for m in out[1:]] == ["user", "assistant", "user"]
    assert "5" in out[-1]["content"]

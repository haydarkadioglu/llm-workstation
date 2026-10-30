import base64

import numpy as np
import pytest

from workstation.engine.base import StopFilter
from workstation.engine.gguf import default_n_ctx, shard_names, split_gguf_id
from workstation.engine.template import ChatRenderer, CHATML_TEMPLATE, normalize_messages


def test_split_gguf_id_keeps_subfolders():
    assert split_gguf_id("org/repo/q4/model.gguf") == ("org/repo", "q4/model.gguf")
    with pytest.raises(ValueError):
        split_gguf_id("model.gguf")


def test_shard_names_expand():
    assert shard_names("m-00001-of-00003.gguf") == [
        "m-00001-of-00003.gguf",
        "m-00002-of-00003.gguf",
        "m-00003-of-00003.gguf",
    ]
    assert shard_names("m.gguf") == ["m.gguf"]


def test_default_ctx_respects_training_limit_and_memory():
    assert default_n_ctx(4096, None, 1000) == 4096
    assert default_n_ctx(131072, None, 1000) == 8192
    assert default_n_ctx(131072, 20000, 2000) == 32768


@pytest.mark.parametrize("size", [1, 2, 5, 100])
def test_stop_filter_cuts_at_marker(size):
    text = "hello STOP world"
    stops = StopFilter(["STOP"])
    out = "".join(stops.feed(text[i : i + size]) for i in range(0, len(text), size)) + stops.flush()
    assert out == "hello "
    assert stops.stopped


def test_stop_filter_passes_through_without_stops():
    stops = StopFilter([])
    assert stops.feed("abc") == "abc"


def test_normalize_decodes_arguments_and_fills_content():
    out = normalize_messages([
        {
            "role": "assistant",
            "content": None,
            "thinking": "hidden",
            "tool_calls": [{"id": "1", "type": "function", "function": {"name": "f", "arguments": '{"a": 1}'}}],
        }
    ])
    assert out[0]["content"] == ""
    assert "thinking" not in out[0]
    assert out[0]["tool_calls"][0]["function"]["arguments"] == {"a": 1}


def test_normalize_flattens_images_to_text():
    parts = [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": "x"}}]
    assert normalize_messages([{"role": "user", "content": parts}])[0]["content"] == "look"


def test_renderer_passes_tools_and_prefers_tool_use_variant():
    renderer = ChatRenderer({"default": "plain", "tool_use": "{{ tools[0].name }}"})
    assert renderer.render([], [{"name": "t"}]) == "t"
    assert renderer.render([]) == "plain"


def test_renderer_moves_system_into_user_when_template_rejects_it():
    template = (
        "{% if messages[0]['role'] == 'system' %}{{ raise_exception('no system') }}{% endif %}"
        "{{ messages[0]['content'] }}"
    )
    renderer = ChatRenderer({"default": template})
    out = renderer.render([{"role": "system", "content": "S"}, {"role": "user", "content": "U"}])
    assert out == "S\n\nU"


def test_chatml_fallback_renders():
    out = ChatRenderer({"default": CHATML_TEMPLATE}).render([{"role": "user", "content": "hi"}])
    assert out.endswith("<|im_start|>assistant\n")


def test_merge_videos_keeps_brightness():
    imageio = pytest.importorskip("imageio.v2")
    pytest.importorskip("imageio_ffmpeg")
    from workstation.engine.video import _encode_mp4, merge_videos

    frame = np.full((64, 64, 3), 128, dtype=np.uint8)
    clip = _encode_mp4([frame] * 4, 8)
    merged = merge_videos([clip, "data:video/mp4;base64," + clip], 8)

    import tempfile, os

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        path = os.path.join(tmp, "m.mp4")
        with open(path, "wb") as handle:
            handle.write(base64.b64decode(merged))
        with imageio.get_reader(path, format="ffmpeg") as reader:
            frames = [frame for frame in reader]
    assert len(frames) == 8
    assert 110 < frames[0].mean() < 145

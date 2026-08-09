"""Unit tests for thumbnail.py's per-size render() arguments.

Unlike test_slice.py's e2e suite, these don't need a live API or the real
OrcaSlicer binary -- they mock gcode_xray_thumbnail.render to check exactly
what arguments each thumbnail size is rendered with.
"""
from unittest.mock import patch

from PIL import Image

import thumbnail


def _fake_render(gcode_text, size=None, **kwargs):
    calls.append((size, kwargs))
    return Image.new("RGBA", size)


calls = []


def test_32x32_thumbnail_uses_zero_margin_others_use_default():
    calls.clear()
    with patch("thumbnail.render", side_effect=_fake_render):
        thumbnail.inject_xray_thumbnails("; no header block, just checking render() args\n")

    by_size = dict(calls)
    assert set(by_size) == {(300, 300), (400, 300), (32, 32)}
    assert by_size[(32, 32)] == {"margin": 0}, (
        "32x32 thumbnail should render with margin=0 so the tiny icon isn't "
        "mostly empty padding"
    )
    assert by_size[(300, 300)] == {}
    assert by_size[(400, 300)] == {}

"""Embeds X-ray toolpath thumbnails in GCODE produced by OrcaSlicer.

Uses gcode-xray-thumbnail (https://github.com/zvakanaka/gcode-xray-thumbnail) to render
an isometric image from the sliced GCODE's own extrusion moves, then writes it as a
"; THUMBNAIL_BLOCK_START ... THUMBNAIL_BLOCK_END" comment block in the same
base64-PNG-in-comments format OrcaSlicer's GUI uses, so slicer/printer UIs that read those
blocks pick up the X-ray version.

The OrcaSlicer CLI (unlike the GUI) never renders its own thumbnails -- there's no
"--thumbnails" flag and no THUMBNAIL_BLOCK in CLI-sliced GCODE at all, since thumbnail
generation normally happens via the GUI's 3D viewport, which headless CLI slicing doesn't
have. So this always inserts fresh blocks after HEADER_BLOCK_END; it only takes the
replace-in-place path if a future OrcaSlicer version starts emitting its own blocks.
"""

import base64
import logging
import re
from io import BytesIO

from gcode_xray_thumbnail import render

log = logging.getLogger(__name__)

# OrcaSlicer wraps each thumbnail's base64 payload at 78 chars per comment line.
THUMBNAIL_LINE_WIDTH = 78

XRAY_SIZES = [(300, 300), (400, 300), (32, 32)]


def _thumbnail_block(width, height, image):
    buf = BytesIO()
    image.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    lines = [b64[i:i + THUMBNAIL_LINE_WIDTH] for i in range(0, len(b64), THUMBNAIL_LINE_WIDTH)]
    body = "\n".join(f"; {line}" for line in lines)
    return (
        "; THUMBNAIL_BLOCK_START\n\n"
        ";\n"
        f"; thumbnail begin {width}x{height} {len(b64)}\n"
        f"{body}\n"
        "; thumbnail end\n"
        "; THUMBNAIL_BLOCK_END\n"
    )


def _block_pattern(width, height):
    return re.compile(
        rf"; THUMBNAIL_BLOCK_START\n\n;\n; thumbnail begin {width}x{height} \d+\n"
        rf"(?:; .*\n)*?; thumbnail end\n; THUMBNAIL_BLOCK_END\n"
    )


_HEADER_END_RE = re.compile(r"; HEADER_BLOCK_END\n\n")


def inject_xray_thumbnails(gcode_text, sizes=XRAY_SIZES):
    """Embed an X-ray render of `gcode_text` for each of `sizes`.

    An existing THUMBNAIL_BLOCK for a given size is replaced in place. Sizes with no
    existing block (the normal case -- see module docstring) are inserted together as new
    blocks right after HEADER_BLOCK_END, matching where OrcaSlicer's GUI places them.
    """
    original = gcode_text
    missing = []
    for width, height in sizes:
        pattern = _block_pattern(width, height)
        if not pattern.search(gcode_text):
            missing.append((width, height))
            continue
        image = render(original, size=(width, height))
        gcode_text = pattern.sub(_thumbnail_block(width, height, image), gcode_text, count=1)

    if missing:
        new_blocks = "".join(
            _thumbnail_block(width, height, render(original, size=(width, height))) + "\n"
            for width, height in missing
        )
        if _HEADER_END_RE.search(gcode_text):
            gcode_text = _HEADER_END_RE.sub(lambda m: m.group(0) + new_blocks, gcode_text, count=1)
        else:
            gcode_text = new_blocks + gcode_text

    return gcode_text

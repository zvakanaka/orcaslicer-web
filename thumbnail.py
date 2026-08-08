"""Replaces OrcaSlicer's rendered-preview thumbnails in GCODE with X-ray toolpath renders.

Uses gcode-xray-thumbnail (https://github.com/zvakanaka/gcode-xray-thumbnail) to render
an isometric image from the sliced GCODE's own extrusion moves, then splices it into the
existing "; THUMBNAIL_BLOCK_START ... THUMBNAIL_BLOCK_END" comment blocks OrcaSlicer wrote,
in the same base64-PNG-in-comments format so slicer/printer UIs that read those blocks pick
up the X-ray version without any other change to the file.
"""

import base64
import logging
import re
from io import BytesIO

from gcode_xray_thumbnail import render

log = logging.getLogger(__name__)

# OrcaSlicer wraps each thumbnail's base64 payload at 78 chars per comment line.
THUMBNAIL_LINE_WIDTH = 78

XRAY_SIZES = [(400, 300), (32, 32)]


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


def inject_xray_thumbnails(gcode_text, sizes=XRAY_SIZES):
    """Replace the THUMBNAIL_BLOCK for each of `sizes` with an X-ray render of `gcode_text`.

    Sizes without a matching existing block are left alone (nothing to replace).
    """
    for width, height in sizes:
        pattern = _block_pattern(width, height)
        if not pattern.search(gcode_text):
            continue
        image = render(gcode_text, size=(width, height))
        gcode_text = pattern.sub(_thumbnail_block(width, height, image), gcode_text, count=1)
    return gcode_text

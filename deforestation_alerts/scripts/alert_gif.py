"""An alert's GIF: Earth Engine renders each frame, Pillow assembles them.

``frame_image`` and ``fetch_frame`` are the only Earth Engine calls. The
frame's imagery is the panes' own (``imagery.rebuild_image``), visualized with
their true-colour parameters, so the GIF shows what the reviewer looked at.
The outline is painted in Earth Engine, where it is georeferenced exactly; the
date label is drawn by Pillow, which Earth Engine has no text for.
"""

import io
import urllib.request
from pathlib import Path
from typing import Optional

import ee
from PIL import Image, ImageDraw, ImageFont

#: the theme's primary colour (``timeline.SELECTED_COLOR`` without the "#")
OUTLINE_COLOR = "bf8f2d"
OUTLINE_PX = 2
FRAME_PX = 512
FRAME_MS = 1000
_LABEL_PX = 16
_PAD = 6
_MARGIN = 8


def frame_image(image, vis: dict, outline: Optional["ee.Geometry"]):
    """``image`` as RGB, with ``outline`` drawn on it when given."""
    rgb = image.visualize(**vis)
    if outline is None:
        return rgb
    edge = (ee.Image().byte()
            .paint(ee.FeatureCollection([ee.Feature(outline)]), 1, OUTLINE_PX)
            .visualize(palette=[OUTLINE_COLOR]))
    return rgb.blend(edge)


def fetch_frame(image, region, *, timeout: float = 120) -> bytes:
    """One frame as PNG bytes: the thumbnail of ``image`` over ``region``."""
    url = image.getThumbURL({"region": region, "dimensions": FRAME_PX, "format": "png"})
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - an Earth Engine URL
        return resp.read()


def _labelled(png: bytes, label: str, size) -> Image.Image:
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    if size is not None and im.size != size:
        im = im.resize(size)
    font = ImageFont.load_default(size=_LABEL_PX)
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
    box = (_MARGIN, _MARGIN, _MARGIN + (right - left) + 2 * _PAD, _MARGIN + (bottom - top) + 2 * _PAD)
    draw.rectangle(box, fill=(0, 0, 0, 160))
    draw.text((_MARGIN + _PAD - left, _MARGIN + _PAD - top), label, font=font, fill=(255, 255, 255, 255))
    return Image.alpha_composite(im, layer).convert("RGB")


def assemble_gif(pngs: list, labels: list, dest) -> Path:
    """Write the labelled frames to ``dest`` as a looping GIF, a second each.

    The format is forced: ``dest`` is usually ``local_files.write_into``'s
    ``.part`` name, which Pillow could not infer a format from."""
    if len(pngs) != len(labels) or len(pngs) < 2:
        raise ValueError(f"a GIF needs at least two labelled frames, got {len(pngs)} / {len(labels)}")
    first = _labelled(pngs[0], labels[0], None)
    frames = [first] + [_labelled(p, label, first.size) for p, label in zip(pngs[1:], labels[1:])]
    palette = [f.convert("P", palette=Image.Palette.ADAPTIVE) for f in frames]
    palette[0].save(dest, format="GIF", save_all=True, append_images=palette[1:],
                    duration=FRAME_MS, loop=0)
    return Path(dest)

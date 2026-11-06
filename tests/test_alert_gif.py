"""The GIF's assembly (Pillow) and the thumbnail fetch, offline."""

import io

import pytest
from PIL import Image

from deforestation_alerts.scripts import alert_gif


def _png(color, size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_the_gif_has_one_frame_per_image_a_second_each_and_loops(tmp_path):
    dest = tmp_path / "a.gif.part"  # write_into's name: the format must not come from it
    alert_gif.assemble_gif([_png((0, 128, 0)), _png((0, 0, 128)), _png((128, 0, 0))],
                           ["one", "two", "three"], dest)
    with Image.open(dest) as im:
        assert im.format == "GIF"
        assert im.n_frames == 3
        assert im.info["duration"] == alert_gif.FRAME_MS
        assert im.info["loop"] == 0


def test_each_frame_carries_its_label_in_the_top_left_corner(tmp_path):
    dest = tmp_path / "a.gif"
    green = (0, 160, 0)
    alert_gif.assemble_gif([_png(green, (200, 120)), _png(green, (200, 120))], ["Before", "After"], dest)
    with Image.open(dest) as im:
        im.seek(0)
        rgb = im.convert("RGB")
        corner = [rgb.getpixel((x, y)) for x in range(4, 80) for y in range(4, 30)]
        far = rgb.getpixel((190, 110))
    assert far == pytest.approx(green, abs=12)  # the image itself is untouched there
    assert any(sum(px) > 600 for px in corner)  # white text
    assert any(sum(px) < 200 for px in corner)  # on the dark box


def test_frames_of_another_size_are_fitted_to_the_first(tmp_path):
    dest = tmp_path / "a.gif"
    alert_gif.assemble_gif([_png((0, 0, 0), (64, 48)), _png((0, 0, 0), (60, 50))], ["a", "b"], dest)
    with Image.open(dest) as im:
        im.seek(1)
        assert im.size == (64, 48)


def test_fewer_than_two_frames_or_unmatched_labels_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        alert_gif.assemble_gif([_png((0, 0, 0))], ["a"], tmp_path / "a.gif")
    with pytest.raises(ValueError):
        alert_gif.assemble_gif([_png((0, 0, 0)), _png((0, 0, 0))], ["a"], tmp_path / "a.gif")


def test_a_frame_is_fetched_as_a_png_thumbnail_of_the_region(tmp_path):
    png = _png((1, 2, 3))
    src = tmp_path / "thumb.png"
    src.write_bytes(png)
    seen = {}

    class FakeImage:
        def getThumbURL(self, params):
            seen.update(params)
            return src.as_uri()

    region = object()
    assert alert_gif.fetch_frame(FakeImage(), region) == png
    assert seen == {"region": region, "dimensions": alert_gif.FRAME_PX, "format": "png"}

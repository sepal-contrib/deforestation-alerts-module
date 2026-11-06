"""cnn_worker: consolidated CNN inference service (D3).

We avoid downloading the real .h5/.keras models in unit tests; we cover the pure,
fast pieces: the serializable RepeatElements layer and save_prediction_prob's
GeoTIFF writer (shape/dtype/transform preserved)."""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from deforestation_alerts.scripts.cnn_worker import (
    RepeatElements,
    save_prediction_prob,
)


def test_repeat_elements_is_keras_serializable():
    import tensorflow as tf  # noqa: F401
    layer = RepeatElements(rep=3)
    cfg = layer.get_config()
    assert cfg["rep"] == 3
    RepeatElements.from_config(cfg)  # round-trips


def _write_src(tmp_path):
    arr = np.zeros((1, 8, 8), dtype="uint8")
    transform = from_origin(10.0, 0.0, 0.01, 0.01)
    p = tmp_path / "src.tif"
    with rasterio.open(p, "w", driver="GTiff", height=8, width=8, count=1,
                       dtype="uint8", crs="EPSG:4326", transform=transform) as d:
        d.write(arr)
    return str(p)


def test_save_prediction_prob_writes_float32_geotiff(tmp_path):
    src = _write_src(tmp_path)
    pred = np.random.rand(8, 8).astype("float32")
    out = str(tmp_path / "pred.tif")
    save_prediction_prob(src, pred, out)

    with rasterio.open(src) as s, rasterio.open(out) as o:
        assert (o.height, o.width) == (s.height, s.width)
        assert o.transform == s.transform
        assert o.dtypes[0] == "float32"
        assert np.allclose(o.read(1), pred)


def test_a_failed_save_leaves_the_previous_prediction_and_no_part(tmp_path):
    """Review A M6: the prediction is written to a ``.part`` and moved into
    place; it used to delete the old file, then write in place."""
    import pytest

    src = _write_src(tmp_path)
    out = tmp_path / "pred.tif"
    first = np.full((8, 8), 0.5, "float32")
    save_prediction_prob(src, first, str(out))
    with pytest.raises(Exception):
        save_prediction_prob(src, None, str(out))  # no array: the write fails
    with rasterio.open(out) as o:
        assert np.allclose(o.read(1), first)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pred.tif", "src.tif"]


def test_a_coarser_composite_is_read_at_the_model_scale(tmp_path):
    """A 10 m composite (Sentinel-2) is upsampled, nearest neighbour, to the
    CNN's 4.77 m over the same extent; a 4.77 m one is read as it is."""
    from deforestation_alerts.scripts.cnn_worker import MODEL_SCALE_M, read_at_model_scale

    def write(res, arr):
        p = tmp_path / f"c{res}.tif"
        with rasterio.open(p, "w", driver="GTiff", height=arr.shape[1], width=arr.shape[2], count=arr.shape[0],
                           dtype="uint8", crs="EPSG:3857", transform=from_origin(1000.0, 2000.0, res, res)) as d:
            d.write(arr)
        return str(p)

    arr = np.arange(2 * 24 * 24, dtype="uint8").reshape(2, 24, 24)
    bands, meta = read_at_model_scale(write(10.0, arr))
    assert bands.shape == (2, 50, 50) and (meta["width"], meta["height"]) == (50, 50)
    assert meta["transform"].a == pytest.approx(MODEL_SCALE_M, abs=0.05)
    assert (meta["transform"].c, meta["transform"].f) == (1000.0, 2000.0)
    assert bands[0, 0, 0] == arr[0, 0, 0] and bands[1, -1, -1] == arr[1, -1, -1]

    same, meta = read_at_model_scale(write(4.77, arr))
    assert np.array_equal(same, arr) and meta["width"] == 24

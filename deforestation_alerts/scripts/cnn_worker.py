"""Consolidated CNN inference service (D3).

Single home for code previously duplicated across model_worker.py and
mosaics_helper. Pure CPU/IO: no solara, no GEE -- spec §4.2 service rule.
"""

import functools
import os
import threading

import rasterio
from rasterio.enums import Resampling
import numpy as np
import tensorflow as tf
from tensorflow.keras import layers, models
from tensorflow.keras import backend as K
from MightyMosaic import MightyMosaic

from deforestation_alerts.errors import ProjectRuleError
from deforestation_alerts.scripts import local_files

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"


@tf.keras.utils.register_keras_serializable()
class RepeatElements(layers.Layer):
    def __init__(self, rep, axis=3, **kwargs):
        super(RepeatElements, self).__init__(**kwargs)
        self.rep = rep
        self.axis = axis

    def call(self, inputs):
        return K.repeat_elements(inputs, self.rep, self.axis)

    def compute_output_shape(self, input_shape):
        shape = list(input_shape)
        shape[self.axis] *= self.rep
        return tuple(shape)

    def get_config(self):
        config = super(RepeatElements, self).get_config()
        config.update({"rep": self.rep, "axis": self.axis})
        return config


#: The pixel size the CNNs were trained at (Planet NICFI), in the composite's metres.
MODEL_SCALE_M = 4.77


def read_at_model_scale(path):
    """``(bands, meta)`` of the composite at ``MODEL_SCALE_M``. A composite
    downloaded at its images' own scale (Sentinel-2 10 m, Landsat 15 m) is
    upsampled here, nearest neighbour as Earth Engine's default resampling
    did when it delivered 4.77 m; a 4.77 m one is read as it is."""
    with rasterio.open(path) as src:
        meta = src.meta.copy()
        if abs(src.res[0] - MODEL_SCALE_M) < 0.01:
            return src.read(), meta
        width = max(1, round(src.width * src.res[0] / MODEL_SCALE_M))
        height = max(1, round(src.height * src.res[1] / MODEL_SCALE_M))
        bands = src.read(out_shape=(src.count, height, width), resampling=Resampling.nearest)
        transform = src.transform * src.transform.scale(src.width / width, src.height / height)
    meta.update(width=width, height=height, transform=transform)
    return bands, meta


def save_prediction_prob(original_img, prediction, output_path, meta=None):
    """Save ``prediction`` as a 1-band float32 GeoTIFF on ``original_img``'s grid.

    Written to a ``.part`` beside ``output_path`` and moved into place
    (``local_files.write_into``): a reader never sees half a raster, and a
    failed write leaves the previous prediction as it was.

    Parameters:
    - original_img (str): the input raster's path; its grid is copied.
    - prediction (array): the probabilities, the input's height x width.
    - output_path (str): the output raster's path.
    - meta (dict): the grid to write on instead of ``original_img``'s (the
      composite resampled to the model's scale, ``read_at_model_scale``).
    """
    if meta is None:
        with rasterio.open(original_img) as src1:
            meta = src1.meta
    out_meta = meta.copy()
    out_meta.update(count=1, dtype="float32", driver="GTiff", compress="deflate")
    with local_files.write_into(output_path) as part:
        with rasterio.open(part, "w", **out_meta) as dest:
            dest.write(prediction, indexes=1)
    return output_path


#: one model predicts at a time: a shared Keras model is not safe to call from two threads
_PREDICT_LOCK = threading.Lock()


@functools.lru_cache(maxsize=2)
def _load_model(path, mtime_ns):
    """The model at ``path``, loaded once per file version (``mtime_ns`` keys a re-download)."""
    return models.load_model(path, custom_objects={"RepeatElements": RepeatElements}, compile=False)


def apply_dl_model(image, model, suffix):
    if not os.path.exists(image):
        raise ProjectRuleError("First download selected images.", key="errors.no_images")

    # Input large image
    input_image = os.path.abspath(image)
    # Input model
    model_path = os.path.abspath(model)

    bands, grid = read_at_model_scale(input_image)
    img = np.transpose(bands / 255, (1, 2, 0)).astype(np.float32)

    mosaic = MightyMosaic.from_array(img, (256, 256), overlap_factor=2)
    with _PREDICT_LOCK:
        model1 = _load_model(model_path, os.stat(model_path).st_mtime_ns)
        prediction1 = mosaic.apply(
            lambda x: model1.predict(x, verbose=0), progress_bar=False, batch_size=2
        )
    final_prediction1 = prediction1.get_fusion()

    # Create the new filename with "_prediction" suffix
    directory, filename = os.path.split(input_image)
    new_filename = os.path.join(
        directory, f"{os.path.splitext(filename)[0]}_prediction{suffix}.tif"
    )

    output = save_prediction_prob(
        input_image, np.squeeze(final_prediction1), new_filename, meta=grid
    )
    return output

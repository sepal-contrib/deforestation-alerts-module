"""Consolidated CNN inference service (D3).

Single home for code previously duplicated across model_worker.py and
mosaics_helper. Pure CPU/IO: no solara, no GEE -- spec §4.2 service rule.
"""

import os
import rasterio
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


def save_prediction_prob(original_img, prediction, output_path):
    """Save ``prediction`` as a 1-band float32 GeoTIFF on ``original_img``'s grid.

    Written to a ``.part`` beside ``output_path`` and moved into place
    (``local_files.write_into``): a reader never sees half a raster, and a
    failed write leaves the previous prediction as it was.

    Parameters:
    - original_img (str): the input raster's path; its grid is copied.
    - prediction (array): the probabilities, the input's height x width.
    - output_path (str): the output raster's path.
    """
    with rasterio.open(original_img) as src1:
        out_meta = src1.meta.copy()
    out_meta.update(count=1, dtype="float32", driver="GTiff")
    with local_files.write_into(output_path) as part:
        with rasterio.open(part, "w", **out_meta) as dest:
            dest.write(prediction, indexes=1)
    return output_path


def apply_dl_model(image, model, suffix):
    def read_image(file_path):
        file_path = file_path  # .decode('utf-8')
        with rasterio.open(file_path) as src:
            img_array = src.read()
        img_array2 = img_array / 255
        img_array3 = np.transpose(img_array2, (1, 2, 0))
        return img_array3.astype(np.float32)

    if not os.path.exists(image):
        raise ProjectRuleError("First download selected images.", key="errors.no_images")

    # Input large image
    input_image = os.path.abspath(image)
    # Input model
    model_path = os.path.abspath(model)

    img = read_image(input_image)

    model1 = models.load_model(
        model_path, custom_objects={"RepeatElements": RepeatElements}, compile=False
    )
    mosaic = MightyMosaic.from_array(img, (256, 256), overlap_factor=2)
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
        input_image, np.squeeze(final_prediction1), new_filename
    )
    return output

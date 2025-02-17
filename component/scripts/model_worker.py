import os
import rasterio
import numpy as np
import tensorflow as tf
from tensorflow.keras import layers, models
from tensorflow.keras import backend as K
from MightyMosaic import MightyMosaic

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"


def save_prediction_prob(original_img, prediction, output_path):

    """
    Save prediction as geotiff .

    Parameters:
    - original_img (list of str): Input raster file path.
    - prediction (object)
    - output_path (str): Output raster file path.
    """
    # If the output file already exists, remove it.
    if os.path.exists(output_path):
        os.remove(output_path)
    
    # Open the input rasters
    src1 = rasterio.open(original_img)

    # Read metadata of the first raster
    out_meta = src1.meta.copy()

    # Reclassify values: set all values greater than 0.2 to 1, and others to 0
    # prediction_reclassified = np.where(prediction > threshold, 1, 0)

    # Update metadata to reflect the new number of bands

    out_meta.update(count=1)
    out_meta.update({"dtype": "float32"})

    # Write the stacked raster to the output file
    with rasterio.open(output_path, "w", **out_meta) as dest:
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
        raise Exception("First download selected images.")

    # Input large image
    input_image = os.path.abspath(image)
    # Input model
    model_path = os.path.abspath(model)

    # size of patches
    patch_size = 256

    # Number of classes
    n_classes = 1

    img = read_image(input_image)

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

    model1 = models.load_model(
        model_path, custom_objects={"RepeatElements": RepeatElements}, compile=False
    )
    mosaic = MightyMosaic.from_array(img, (256, 256), overlap_factor=2)
    prediction1 = mosaic.apply(
        lambda x: model1.predict(x, verbose=0), progress_bar=False, batch_size=2
    )
    final_prediction1 = prediction1.get_fusion()

    # Create the new filename with "_processed" suffix
    directory, filename = os.path.split(input_image)
    new_filename = os.path.join(
        directory, f"{os.path.splitext(filename)[0]}_prediction{suffix}.tif"
    )

    output = save_prediction_prob(
        input_image, np.squeeze(final_prediction1), new_filename
    )
    return output

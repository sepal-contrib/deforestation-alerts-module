from sepal_ui import model
from traitlets import Any, Unicode
from component.message import cm


class AoiDateModel(model.Model):

    ############################################################################
    # aoi and dates inputs
    ############################################################################
    aoi = Any(None).tag(sync=True)
    "the aoi to search alert in"

    start_date = Unicode("").tag(sync=True)
    "the start date of the retreived alerts (YYYY-MM-DD)"

    end_date = Unicode("").tag(sync=True)
    "the end date of the retreived alerts (YYYY-MM-DD)"

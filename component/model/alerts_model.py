from sepal_ui import model
from traitlets import Any, Int, Float

from component import parameter as cp
from component.message import cm


class AlertFilterModel(model.Model):

    ############################################################################
    # alert filter section files
    ############################################################################
    available_alerts_list = Any("").tag(sync=True)
    "List of alert collections that has alerts in the aoi and dates selected by the users"

    available_alerts_raster_list = Any("").tag(sync=True)
    "List of alert collections that has alerts in the aoi and dates selected by the users"

    rnd_number = Any(None).tag(sync=True)
    "just to launch change in trait"


class SelectedAlertsModel(model.Model):

    ############################################################################
    # selected alerts
    ############################################################################
    alert_selection_polygons = Any(None).tag(sync=True)
    "User drawn polygons used for filtering alerts in case this method of filtering is selected"

    min_area = Any(None).tag(sync=True)
    "Minimum area for alerts set by user"

    max_number_alerts = Any(0).tag(sync=True)
    "Max number of alerts set by user"

    alert_selection_area = Any(None).tag(sync=True)
    "User alerts selection methond for selecting alerts"

    alert_sorting_method = Any(None).tag(sync=True)
    "User alerts selection methond for ordering alerts"

    alerts_total_bbs = Any(None).tag(sync=True)
    "Feature Collection that cointains the bounding boxes fro the whole area of alerts filtered, they may or may not have a priority field"
    "List of needed fields, valid, before_img, after_img, min_date, max_date, alert_polygon_dir, area, description"

    alerts_bbs = Any(None).tag(sync=True)
    "Feature Collection that cointains bounding boxes of first x filtered alerts, they may or may not have a priority field"
    "List of needed fields, valid, before_img, after_img, min_date, max_date, alert_polygon_dir, area, description"


class ReviewAlertsModel(model.Model):

    ############################################################################
    # selected alerts
    ############################################################################
    nTotalAlerts = Int(None).tag(sync=True)
    "Total Alerts Number"

    nReviewedAlerts = Int(None).tag(sync=True)
    "Reviewed Alerts Number"

    nConfirmedAlerts = Int(None).tag(sync=True)
    "Confirmed Alerts Number"

    nFalseAlerts = Int(None).tag(sync=True)
    "False positive Alerts Number"

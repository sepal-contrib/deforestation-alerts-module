from sepal_ui import model
from traitlets import Any, Int, Float, Unicode

from component import parameter as cp
from component.message import cm


class AlertFilterModel(model.Model):

    ############################################################################
    # alert filter section files
    ############################################################################
    alerts_dictionary = Any("").tag(sync=True)
    "Dictionary of alert collections with dates"

    available_alerts_list = Any("").tag(sync=True)
    "List of alert collections that has alerts in the aoi and dates selected by the users"

    available_alerts_raster_list = Any("").tag(sync=True)
    "Dictionary of alert rasters in the aoi and dates selected by the users"

    def reset_model(self):
        self.alerts_dictionary = ""
        self.available_alerts_list = ""
        self.available_alerts_raster_list = ""


class SelectedAlertsModel(model.Model):

    ############################################################################
    # selected alerts
    ############################################################################
    selected_alert_sources = Any(None).tag(sync=True)
    "Alert sources selected by the user"

    alert_selection_polygons = Any(None).tag(sync=True)
    "User drawn polygons used for filtering alerts in case this method of filtering is selected"

    min_area = Float(None).tag(sync=True)
    "Minimum area for alerts set by user"

    max_number_alerts = Any(0).tag(sync=True)
    "Max number of alerts set by user"

    alert_selection_area = Any(None).tag(sync=True)
    "User alerts selection methond for selecting alerts"

    alert_selection_area_n = Int(0).tag(sync=True)
    "User alerts selection methond for selecting alerts, numeric so we can save to dictionary and keep it language independant"

    alert_sorting_method = Any(None).tag(sync=True)
    "User alerts selection methond for ordering alerts"

    alert_sorting_method_n = Int(0).tag(sync=True)
    "User alerts selection methond for ordering alerts, numeric so we can save to dictionary and keep it language independant"

    filtered_alert_raster = Any(None).tag(sync=True)
    "Alert raster image with all user filters applied"

    alerts_total_bbs = Any(None).tag(sync=True)
    "Feature Collection that cointains the bounding boxes fro the whole area of alerts filtered, they may or may not have a priority field"
    "List of needed fields, valid, before_img, after_img, min_date, max_date, alert_polygon_dir, area, description"

    alerts_bbs = Any(None).tag(sync=True)
    "Feature Collection that cointains bounding boxes of first x filtered alerts, they may or may not have a priority field"
    "List of needed fields, valid, before_img, after_img, min_date, max_date, alert_polygon_dir, area, description"

    received_alerts = Any(None).tag(sync=True)
    "Variable used to indicate that alerts in json format where received"

    def export_dictionary(self):
        dictionary = {
            "selected_alert_sources": self.selected_alert_sources,
            "alert_selection_polygons": self.alert_selection_polygons,
            "min_area": self.min_area,
            "max_number_alerts": self.max_number_alerts,
            "alert_selection_area": self.alert_selection_area_n,
            "alert_sorting_method": self.alert_sorting_method_n,
        }
        return dictionary

    def reset_model(self):
        self.selected_alert_sources = None
        self.alert_selection_polygons = None
        self.min_area = None
        self.max_number_alerts = 0
        self.alert_selection_area = None
        self.alert_selection_area_n = 0
        self.alert_sorting_method = None
        self.filtered_alert_raster = None
        self.alerts_total_bbs = None
        self.alerts_bbs = None
        self.received_alerts = None

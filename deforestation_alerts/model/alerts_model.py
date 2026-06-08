from pysepal import model
from traitlets import Any, Int, Float, Unicode

from deforestation_alerts import parameter as cp
from deforestation_alerts.message import cm


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

    def import_from_dictionary(self, data):
        """Restore the saved filter selections (inverse of export_dictionary).

        Mirrors export_dictionary: the numeric (language-independent) codes are
        read back into the ``*_n`` traits. The non-numeric area/sorting labels
        are reconstructed by the Filter UI on render, so only the codes are
        restored here.

        Assignments are guarded with ``key in data`` (not ``data.get(key,
        self.x)``): ``min_area`` is a ``Float(None)`` whose default cannot be
        read on a fresh model under the installed traitlets (it raises
        TraitError), so referencing ``self.min_area`` as a get() default would
        crash even when the key is present. ``import json`` makes the except
        clause reachable without a NameError (same fix as the other importers).
        """
        import json

        try:
            if "selected_alert_sources" in data:
                self.selected_alert_sources = data["selected_alert_sources"]
            if "alert_selection_polygons" in data:
                self.alert_selection_polygons = data["alert_selection_polygons"]
            if data.get("min_area") is not None:
                self.min_area = data["min_area"]
            if "max_number_alerts" in data:
                self.max_number_alerts = data["max_number_alerts"]
            if "alert_selection_area" in data:
                self.alert_selection_area_n = data["alert_selection_area"]
            if "alert_sorting_method" in data:
                self.alert_sorting_method_n = data["alert_sorting_method"]
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error loading JSON file: {e}")

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

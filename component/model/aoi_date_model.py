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

    def export_dictionary(self):
        dictionary = {
            #'aoi' : self.aoi,
            "start_date": self.start_date,
            "end_date": self.end_date,
        }
        return dictionary

    def import_from_dictionary(self, file_path):
        """
        Import class attributes from a JSON file.
        Args:
            file_path (str): Path to the JSON file.
        """
        try:
            # with open(file_path, 'r') as f:
            #     data = json.load(f)
            data = file_path
            # Update attributes
            # self.aoi = data.get("aoi", self.aoi)
            self.start_date = data.get("start_date", self.start_date)
            self.end_date = data.get("end_date", self.end_date)

        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error loading JSON file: {e}")

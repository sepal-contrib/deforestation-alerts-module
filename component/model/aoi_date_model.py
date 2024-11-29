from sepal_ui import model
from sepal_ui.aoi.aoi_model import AoiModel
from traitlets import Any, Unicode
from component.message import cm



class AoiDateModel(AoiModel):
    """
    Extend the AoiModel class to include start_date and end_date attributes
    for specifying the date range of retrieved alerts.
    """

    # Add start_date and end_date attributes
    start_date = Unicode("").tag(sync=True)
    end_date = Unicode("").tag(sync=True)

    # Optional: Add docstrings or comments for clarity
    """
    start_date: str
        The start date of the retrieved alerts (format: YYYY-MM-DD).
    end_date: str
        The end date of the retrieved alerts (format: YYYY-MM-DD).
    """
    def export_dictionary(self):
        dictionary = {
            'aoi_admin' : self.admin,
            'aoi_asset_name' : self.asset_name,
            'aoi_method' : self.method,
            'aoi_asset_json' : self.asset_json,
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
            self.aoi_admin = data.get("aoi_admin", self.aoi_admin)
            self.aoi_asset_name = data.get("aoi_asset_name", self.aoi_asset_name)
            self.aoi_method = data.get("aoi_method", self.aoi_method)
            self.aoi_asset_json = data.get("aoi_asset_json", self.aoi_asset_json)
            self.start_date = data.get("start_date", self.start_date)
            self.end_date = data.get("end_date", self.end_date)

        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error loading JSON file: {e}")
    
    

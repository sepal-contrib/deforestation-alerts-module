import json

from pysepal import model
from pysepal.aoi.aoi_model import AoiModel
from traitlets import Any, Unicode
from deforestation_alerts.message import cm


class AoiDateModel(AoiModel):
    """
    Extend the AoiModel class to include start_date and end_date attributes
    for specifying the date range of retrieved alerts.
    """

    # Add start_date and end_date attributes.
    # allow_none=True mirrors the base AoiModel string traits (name/admin/method/
    # asset_name are all Unicode(allow_none=True)). This is required when an
    # AoiView is constructed with this model as its `model=`: AoiView.__init__
    # calls model.clear_attributes(), which setattr(None) on EVERY trait. With a
    # plain Unicode("") these two raised TraitError(None) and the AOI tile failed
    # to render. allow_none keeps them consistent with the rest of the AOI model.
    start_date = Unicode("", allow_none=True).tag(sync=True)
    end_date = Unicode("", allow_none=True).tag(sync=True)

    # Optional: Add docstrings or comments for clarity
    """
    start_date: str
        The start date of the retrieved alerts (format: YYYY-MM-DD).
    end_date: str
        The end date of the retrieved alerts (format: YYYY-MM-DD).
    """

    def export_dictionary(self):
        dictionary = {
            "aoi_admin": self.admin,
            "aoi_asset_name": self.asset_name,
            "aoi_name": self.name,
            "aoi_method": self.method,
            "aoi_asset_json": self.asset_json,
            "aoi_vector_json": self.vector_json,
            "aoi_geo_json": self.geo_json,
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
            self.admin = data.get("aoi_admin", self.admin)
            self.asset_name = data.get("aoi_asset_name", self.asset_name)
            self.method = data.get("aoi_method", self.method)
            self.name = data.get("aoi_name", self.name)
            self.asset_json = data.get("aoi_asset_json", self.asset_json)
            self.vector_json = data.get("aoi_vector_json", self.vector_json)
            self.geo_json = data.get("aoi_geo_json", self.geo_json)
            self.start_date = data.get("start_date", self.start_date)
            self.end_date = data.get("end_date", self.end_date)

            if self.method in ["ADMIN0", "ADMIN1", "ADMIN2"]:
                self._from_admin(self.admin)
            elif self.method == "SHAPE":
                self._from_vector(self.vector_json)
            elif self.method == "DRAW":
                self._from_geo_json(self.geo_json)
            elif self.method == "ASSET":
                self._from_asset(self.asset_json)

            # Recipe load calls the _from_* loaders directly instead of going
            # through AoiModel.set_object(), so nothing bumps ``object_set`` --
            # the one trait that fires *after* both the geometry and the name
            # are materialised. Observers of "the AOI object was (re)set" (the
            # shell's aoi_name mirror, which feeds the pipeline header gates)
            # never hear about a recipe load without this. Bump it last, and
            # unconditionally: a recipe with no AOI method leaves
            # ``feature_collection`` None, which is itself the correct
            # "no AOI selected" signal.
            self.object_set += 1

        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error loading JSON file: {e}")

    def reset_model(self):
        # Delegate to AoiModel.clear_attributes() — the same reset AoiView uses —
        # so EVERY AOI trait is cleared, including the geometry ones
        # (vector_json / geo_json / point_json / feature_collection) and the
        # cached gdf. An earlier explicit reset left those stale, so creating a
        # new recipe after a SHAPE/DRAW session could leak the old geometry into
        # the next saved recipe. clear_attributes() nulls the date traits too, so
        # restore the "" string defaults the rest of the app expects.
        self.clear_attributes()
        self.start_date = ""
        self.end_date = ""

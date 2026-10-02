"""Engine errors carry a message key and values (spec §11); the UI shows them
in the user's language, and a library's own error as its text."""

import ast
import builtins
import importlib
from pathlib import Path

import pytest

from deforestation_alerts.engine import raster_io
from deforestation_alerts.engine.runner import HarmonizeFailed
from deforestation_alerts.errors import KeyedError, ProjectRuleError, UserFileMissing
from gui.i18n import t
from gui.scripts.error_text import error_text

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("deforestation_alerts/engine", "deforestation_alerts/project", "deforestation_alerts/scripts")

#: Raised with an English text on purpose: a caller's programming error,
#: unreachable from the UI. (file, exception, a piece of its message's source)
PROGRAMMER_ERRORS = {
    ("deforestation_alerts/engine/align.py", "ValueError", "only nearest resampling"),
    ("deforestation_alerts/engine/harmonize.py", "ValueError", "mask mode must be one of"),
    ("deforestation_alerts/engine/catalogue.py", "ValueError", "unknown source"),
    ("deforestation_alerts/engine/catalogue.py", "ValueError", "only GLAD-L has catalogue metadata"),
    ("deforestation_alerts/engine/catalogue.py", "ValueError", "unknown date encoding"),
    ("deforestation_alerts/engine/catalogue.py", "ValueError", "preset has no alert band"),
    ("deforestation_alerts/engine/presets.py", "ValueError", "unknown date encoding"),
    ("deforestation_alerts/engine/presets.py", "ValueError", "preset has no alert band"),
    # defensive: the mask check and the AOI-raster build before it record every input
    ("deforestation_alerts/engine/runner.py", "RuntimeError", "its harmonize inputs are not recorded"),
    ("deforestation_alerts/project/models.py", "ValueError", "unknown predefined source"),
    ("deforestation_alerts/project/models.py", "ValueError", "is not a custom source"),
    # the Alerts step offers only the predefined sources not yet added
    ("deforestation_alerts/project/models.py", "ValueError", "is already a source"),
    # the environment (geedim is a dependency) and the export lock's timeout,
    # which only a wedged Earth Engine export reaches
    ("deforestation_alerts/scripts/raster_export.py", "ImportError", "Install geedim"),
    ("deforestation_alerts/scripts/raster_export.py", "TimeoutError", "waiting for another"),
    ("deforestation_alerts/scripts/alert_export.py", "ValueError", "Unsupported export format"),
    ("deforestation_alerts/scripts/raster_export.py", "ValueError", "image must be an ee.Image"),
    ("deforestation_alerts/scripts/custom_sources.py", "ValueError", "custom source slot out of range"),
    ("deforestation_alerts/scripts/report_builder.py", "ValueError", "Input must be a string or a list"),
    ("deforestation_alerts/scripts/mosaics_helper.py", "ValueError", "Unsupported imagery source"),
    ("deforestation_alerts/scripts/mosaics_helper.py", "ValueError", "not found in the GeoDataFrame"),
    ("deforestation_alerts/scripts/mosaics_helper.py", "ValueError", "The input GeoJSON has no features"),
    ("deforestation_alerts/scripts/mosaics_helper.py", "ValueError", "must be a MultiPolygon or Polygon"),
    ("deforestation_alerts/scripts/mosaics_helper.py", "ValueError", "must be a list of dictionaries"),
}


def test_a_keyed_error_keeps_its_english_detail_and_names_its_message():
    err = raster_io.NotEnoughSpaceError("needs 9 GB free in /x, has 1 GB", needed_gb=9, free_gb=1, folder="/x")
    assert isinstance(err, RuntimeError) and isinstance(err, KeyedError)
    assert str(err) == "needs 9 GB free in /x, has 1 GB"
    assert err.key == "errors.not_enough_space" and err.values == {"needed_gb": 9, "free_gb": 1, "folder": "/x"}
    assert error_text(err) == t("errors.not_enough_space", needed_gb=9, free_gb=1, folder="/x")


def test_error_text():
    rule = ProjectRuleError(key="steps.prepare.mask_edit_used")
    assert isinstance(rule, ValueError) and str(rule) == "steps.prepare.mask_edit_used"
    assert error_text(rule) == t("steps.prepare.mask_edit_used")
    assert error_text(ValueError("steps.prepare.mask_edit_used")) == t("steps.prepare.mask_edit_used")
    assert error_text(UserFileMissing("file not found: /a.tif", path="/a.tif")) == t("errors.file_not_found",
                                                                                      path="/a.tif")
    assert error_text(OSError("No space left on device")) == "No space left on device"
    assert error_text(RuntimeError()) == "RuntimeError"
    # a message whose value is missing reads as the English detail, not as its key
    assert error_text(UserFileMissing("file not found: /b.tif")) == "file not found: /b.tif"


def test_a_failed_harmonize_shows_its_causes_message():
    cause = raster_io.GridMismatchError("x.tif is not on the project grid", path="x.tif")
    wrapped = HarmonizeFailed(cause, {})
    assert error_text(wrapped) == t("errors.off_grid", path="x.tif")
    assert error_text(HarmonizeFailed(OSError("disk"), {})) == "disk"


def test_an_earth_engine_asset_not_found_reads_in_words():
    """Earth Engine's own text for a missing or unreadable asset (the modals'
    validation); any other Earth Engine error keeps its text (spec §12)."""
    import ee

    gone = ee.EEException("Image.load: Image asset 'users/me/gone' not found "
                          "(does not exist or caller does not have access).")
    assert error_text(gone) == t("errors.ee_asset_not_found", asset="users/me/gone")
    assert error_text(ee.EEException("Computation timed out.")) == "Computation timed out."


def _raises(rel):
    """``(line, exception name, message source)`` of every raise with a literal message."""
    for node in ast.walk(ast.parse((ROOT / rel).read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args):
            continue
        arg = node.exc.args[0]
        literal = isinstance(arg, ast.JoinedStr) or (isinstance(arg, ast.Constant) and isinstance(arg.value, str))
        func = node.exc.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if literal and name:
            yield node.lineno, name, ast.unparse(arg)


def test_every_error_the_engine_raises_with_a_text_names_a_message():
    """A new raise either is a KeyedError or is listed above, with the reason
    it can never reach a user."""
    offenders = []
    for package in PACKAGES:
        for py in sorted((ROOT / package).rglob("*.py")):
            rel = str(py.relative_to(ROOT))
            module = importlib.import_module(rel[:-3].replace("/", "."))
            for lineno, name, text in _raises(rel):
                cls = getattr(module, name, None) or getattr(builtins, name, None)
                if isinstance(cls, type) and issubclass(cls, KeyedError):
                    continue
                if any(rel == f and name == c and piece in text for f, c, piece in PROGRAMMER_ERRORS):
                    continue
                offenders.append(f"{rel}:{lineno} {name}({text})")
    assert offenders == []


@pytest.mark.parametrize("key", ["errors.not_enough_space", "errors.off_grid", "errors.raw_not_as_asked",
                                 "errors.mask_not_as_asked", "errors.no_aoi_or_grid", "errors.file_not_found",
                                 "errors.not_a_source", "errors.not_ready", "errors.footprint_missing",
                                 "errors.no_sources", "errors.no_drawing", "errors.no_tippecanoe",
                                 "errors.bands_not_loaded", "errors.project_unreadable",
                                 "errors.project_not_a_manifest", "errors.project_newer_schema",
                                 "errors.project_invalid", "errors.no_images", "errors.folder_not_writable",
                                 "errors.source_native_unknown", "errors.attribute_band_missing",
                                 "errors.attribute_no_crs", "errors.ee_asset_not_found"])
def test_every_error_key_exists(key):
    from gui.i18n import has_message

    assert has_message(key)

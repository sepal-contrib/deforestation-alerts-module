"""The attribute-layer modal (spec D3): its refusals, the columns it hands
over (review focus 3) and its primary button. The real AttributeForm lists
Earth Engine assets, so a recorder stands in for it here; its own tests are
in tests/test_integrate_step.py."""

from types import SimpleNamespace

import ipyvuetify as v
import pytest
import reacton
import solara

from deforestation_alerts.scripts.raster_attribute import ALERT_RESERVED_COLUMNS
from gui.i18n import t
from gui.widget import attribute_modal as am
from gui.widget.attribute_form import layer_label
from gui.widget.enter_key import ENTER_DEFAULT

LC = {"source": "/home/u/rasters/landcover.tif", "kind": "local",
      "bands": [{"band": "b1", "stat": "majority", "column": "b1"}]}
DEM = {"source": "users/me/dem", "kind": "gee",
       "bands": [{"band": "b1", "stat": "mean", "column": "b1_2", "crs": "EPSG:4326", "scale": 30.0}]}


def _layer(source, *columns, kind="local"):
    return {"source": source, "kind": kind, "bands": [{"band": c, "stat": "mean", "column": c} for c in columns]}


def test_layer_label_names_a_geotiff_by_its_file_and_an_asset_by_its_id():
    assert layer_label(LC) == "landcover.tif"
    assert layer_label({"source": "projects/x/assets/radd.v2", "kind": "gee"}) == "projects/x/assets/radd.v2"
    assert layer_label(None) == ""
    assert layer_label({**LC, "name": "Cover"}) == "Cover"


def test_attribute_error_refuses_no_bands_and_a_raster_another_layer_reads():
    assert am.attribute_error(None, []) == "steps.integrate.attr_err_no_bands"
    assert am.attribute_error({**LC, "bands": []}, []) == "steps.integrate.attr_err_no_bands"
    assert am.attribute_error(LC, [DEM]) is None
    assert am.attribute_error(LC, [DEM, LC]) == "steps.integrate.attr_err_duplicate"
    assert am.attribute_error(LC, [DEM, LC], editing=1) is None  # the layer itself
    assert am.attribute_error({**LC, "kind": "gee"}, [LC]) is None  # an asset id is not that file


def test_finish_layer_never_takes_an_alert_column_or_another_layers():
    """Review focus 3: b1 -> b1_2 (b1_3 when b1_2 is taken too), count -> count_2."""
    new = _layer("c.tif", "b1", "count")
    assert [b["column"] for b in am.finish_layer(new, [LC])["bands"]] == ["b1_2", "count_2"]
    assert [b["column"] for b in am.finish_layer(new, [LC, DEM])["bands"]] == ["b1_3", "count_2"]
    # an edited layer keeps its own columns: they are not another layer's
    assert am.finish_layer(DEM, [LC, DEM], editing=1) == DEM
    assert am.finish_layer(LC, [LC, DEM], editing=0) == LC


@pytest.fixture
def form(monkeypatch):
    calls = []

    @solara.component
    def _Form(gee_interface, value, on_value, reserved, on_busy, taken_names=()):
        calls.append(SimpleNamespace(value=value, on_value=on_value, reserved=reserved, on_busy=on_busy,
                                     taken_names=taken_names))

    monkeypatch.setattr(am, "AttributeForm", _Form)
    return calls


def _render(layers, editing=None):
    open_, saved = solara.reactive(True), []
    _box, rc = reacton.render(am.AttributeModal(open_=open_, gee_interface=None, layers=layers, editing=editing,
                                                on_save=lambda i, layer: saved.append((i, layer))),
                              handle_error=False)
    return open_, saved, rc


def _strings(widget):
    out = []
    for child in getattr(widget, "children", None) or []:
        out.extend([child] if isinstance(child, str) else _strings(child))
    return out


def _button(rc, label):
    return next(w for w in rc.find(v.Btn).widgets if label in w.children)


def _alerts(rc):
    return [" ".join(_strings(w)) for w in rc.find(v.Alert).widgets]


def test_adding_a_layer_hands_over_unclashed_columns_and_closes(form):
    open_, saved, rc = _render([LC])
    try:
        dialog = rc.find(v.Dialog).widget
        assert dialog.max_width == "560px" and dialog.persistent
        assert t("steps.integrate.attr_add_title") in _strings(rc.container)
        add = _button(rc, t("steps.integrate.attr_add"))
        assert add.disabled and ENTER_DEFAULT in add.class_ and "mdi-plus" in _strings(add)
        assert _alerts(rc) == []  # a form that has read no bands yet is not wrong
        assert form[-1].value is None and form[-1].reserved == ALERT_RESERVED_COLUMNS | {"b1"}
        assert form[-1].taken_names == set()  # LC has no name: nothing for a default to clash with

        form[-1].on_value(_layer("c.tif", "b1", "count"))
        add = _button(rc, t("steps.integrate.attr_add"))
        assert not add.disabled
        add.fire_event("click", None)
        assert saved == [(None, {"source": "c.tif", "kind": "local", "bands": [
            {"band": "b1", "stat": "mean", "column": "b1_2"},
            {"band": "count", "stat": "mean", "column": "count_2"}]})]
        assert open_.value is False
    finally:
        rc.close()


@pytest.mark.parametrize("config, key", [
    ({**LC, "bands": []}, "steps.integrate.attr_err_no_bands"),  # every band unticked
    (LC, "steps.integrate.attr_err_duplicate"),                   # layer 0's raster again
], ids=["no-bands", "duplicate"])
def test_a_layer_that_cannot_be_saved_says_why_and_disables_add(form, config, key):
    open_, saved, rc = _render([LC])
    try:
        form[-1].on_value(config)
        assert _alerts(rc) == [t(key)]
        assert _button(rc, t("steps.integrate.attr_add")).disabled
        assert saved == [] and open_.value is True
    finally:
        rc.close()


def test_a_load_in_progress_takes_save_off_enter(form):
    """Reloading bands mid-edit: Enter must not save the config the form held
    before the load, so Save is disabled and loses the Enter class until the
    load ends."""
    _open, _saved, rc = _render([LC, DEM], editing=1)
    try:
        form[-1].on_busy(True)
        save = _button(rc, t("common.save"))
        assert save.disabled and ENTER_DEFAULT not in (save.class_ or "")
        form[-1].on_busy(False)
        save = _button(rc, t("common.save"))
        assert not save.disabled and ENTER_DEFAULT in save.class_
    finally:
        rc.close()


def test_editing_a_layer_saves_it_in_place_with_its_own_columns(form):
    open_, saved, rc = _render([LC, DEM], editing=1)
    try:
        assert t("steps.integrate.attr_edit_title", name="users/me/dem") in _strings(rc.container)
        assert form[-1].value == DEM and form[-1].reserved == ALERT_RESERVED_COLUMNS | {"b1"}
        save = _button(rc, t("common.save"))
        assert not save.disabled and ENTER_DEFAULT in save.class_
        assert "mdi-content-save-outline" in _strings(save)
        save.fire_event("click", None)
        assert saved == [(1, DEM)] and open_.value is False
    finally:
        rc.close()

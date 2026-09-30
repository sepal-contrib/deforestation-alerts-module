"""CustomSourceList: add / edit / remove custom alert sources in the Aux dialog."""

import ipyvuetify as v
import pytest

from deforestation_alerts.message import cm
from deforestation_alerts.model.aux_model import AuxModel


@pytest.fixture
def make(monkeypatch):
    # AssetSelect lists GEE assets on construction; a plain Select stands in.
    from pysepal import sepalwidgets as sw

    monkeypatch.setattr(
        sw.inputs, "AssetSelect", lambda **kwargs: v.Select(items=[], v_model=None)
    )
    from gui.widget.custom_source_list import CustomSourceList

    def _make(items=()):
        model = AuxModel()
        model.custom_alerts = [dict(i) for i in items]
        return model, CustomSourceList(model)

    return _make


def _rows(widget):
    return [c for c in widget.rows_box.children if not isinstance(c, v.Divider)]


def _fill(widget, name, asset):
    widget.name_field.v_model = name
    widget.asset_field.v_model = asset


TWO = [{"name": "A", "asset": "users/x/a"}, {"name": "B", "asset": "users/x/b"}]


def test_empty_state(make):
    _, w = make()
    assert len(_rows(w)) == 1
    assert cm.aux_tile.custom_empty in str(_rows(w)[0].children)
    assert w.counter.children == [cm.aux_tile.custom_count.format(n=0, max=5)]
    assert not w.add_btn.disabled


def test_add_writes_the_model_and_closes(make):
    model, w = make()
    w.open_add()
    assert w.dialog.v_model is True
    assert w.save_btn.disabled  # nothing filled yet
    _fill(w, "  Sentinel-1 ", "users/x/s1")
    assert not w.save_btn.disabled
    w.save()
    assert model.custom_alerts == [{"name": "Sentinel-1", "asset": "users/x/s1"}]
    assert w.dialog.v_model is False
    assert len(_rows(w)) == 1


def test_edit_keeps_position_and_own_name_is_fine(make):
    model, w = make(TWO)
    w.open_edit(0)
    assert w.name_field.v_model == "A" and w.asset_field.v_model == "users/x/a"
    assert not w.save_btn.disabled  # unchanged own name is not a duplicate
    _fill(w, "A2", "users/x/a2")
    w.save()
    assert model.custom_alerts == [
        {"name": "A2", "asset": "users/x/a2"},
        {"name": "B", "asset": "users/x/b"},
    ]


@pytest.mark.parametrize(
    "name, message",
    [(" b ", "custom_name_duplicate"), ("radd", "custom_name_reserved")],
)
def test_bad_names_disable_save_with_a_message(make, name, message):
    model, w = make(TWO)
    w.open_add()
    _fill(w, name, "users/x/new")
    assert w.save_btn.disabled
    assert w.name_field.error_messages == [getattr(cm.aux_tile, message)]
    w.save()  # ignored while invalid
    assert len(model.custom_alerts) == 2


def test_empty_name_or_missing_asset_disable_save_silently(make):
    _, w = make()
    w.open_add()
    _fill(w, "", "users/x/a")
    assert w.save_btn.disabled and w.name_field.error_messages == []
    _fill(w, "A", None)
    assert w.save_btn.disabled


def test_cap_disables_add(make):
    items = [{"name": f"S{i}", "asset": f"a{i}"} for i in range(5)]
    _, w = make(items)
    assert w.add_btn.disabled
    w.open_add()
    assert w.dialog.v_model is False


def test_remove_after_confirmation(make):
    model, w = make(TWO)
    w.ask_remove(1)
    assert w.confirm.v_model is True
    assert "B" in w.confirm_text.children[0]
    w.cancel_remove()
    assert len(model.custom_alerts) == 2
    w.ask_remove(1)
    w.confirm_remove()
    assert model.custom_alerts == [{"name": "A", "asset": "users/x/a"}]
    assert w.confirm.v_model is False


def test_model_changes_rerender(make):
    model, w = make()
    model.custom_alerts = TWO
    assert len(_rows(w)) == 2
    assert w.counter.children == [cm.aux_tile.custom_count.format(n=2, max=5)]

"""A version 1 recipe in the Project dialog: marked, locked for rename until
opened, converted on its first open, deletable as it is (v1 migration, task 6)."""

import functools
import threading
from dataclasses import replace

import ipyvuetify as v
import pytest

from deforestation_alerts.project import io
from deforestation_alerts.project.v1_migration import is_v1_recipe
from gui.i18n import t
from gui.scripts import v1_open
from gui.tile import project_dialog as pd
from tests._render_helpers import run_rendered
from tests._v1_helpers import write_recipe
from tests.test_manage_projects import _icon_buttons, _info, _render_manage, _texts
from tests.test_project_dialog import (  # noqa: F401 - dialog is a fixture
    SETTLE_SECONDS,
    _button,
    _delete,
    _listed,
    _settle,
    _toasts,
    dialog,
)
from tests.test_v1_open import AOI_FC

NAME = "recipe_2026-09-29-223039"


def _legacy():
    return replace(_info(NAME, periods=1, sets=1), legacy=True)


def _resolver(monkeypatch, resolve):
    monkeypatch.setattr(pd, "ensure_converted", functools.partial(v1_open.ensure_converted, resolve=resolve))


def test_legacy_row_has_the_mark_and_locked_icons():
    ns = _render_manage([_info("v2"), _legacy()])
    try:
        chips = [c for c in ns.rc.find(v.Chip).widgets if c.children == [t("steps.project.v1_mark")]]
        assert len(chips) == 1 and chips[0].outlined and chips[0].x_small
        assert chips[0].attributes["title"] == t("steps.project.v1_hint")
        pencils = _icon_buttons(ns.rc, "mdi-pencil-outline")
        assert [p.disabled for p in pencils] == [False, True]
        assert pencils[1].attributes["title"] == t("steps.project.v1_convert_first")
        assert [b.disabled for b in _icon_buttons(ns.rc, "mdi-delete-outline")] == [False, False]
    finally:
        ns.rc.close()


def test_the_converting_row_says_so():
    ns = _render_manage([_legacy()], converting=NAME)
    try:
        assert t("steps.project.v1_converting", name=NAME) in _texts(ns.box)
    finally:
        ns.rc.close()


def test_open_converts_then_installs(dialog, monkeypatch):  # noqa: F811
    write_recipe(dialog.root / NAME)
    go = threading.Event()

    def held(params):
        go.wait(SETTLE_SECONDS)
        return AOI_FC

    _resolver(monkeypatch, held)

    async def main(rc):
        await _settle(lambda: NAME in (_listed(dialog) or {}), "the recipe listed", rc=rc)
        assert dialog.manage[-1]["infos"][0].legacy
        dialog.manage[-1]["on_select"](NAME)
        dialog.manage[-1]["on_open"]()
        await _settle(lambda: dialog.manage[-1]["converting"] == NAME, "the row says converting", rc=rc)
        go.set()
        await _settle(lambda: getattr(dialog.state.project.value, "name", None) == NAME, "the recipe opened")
        p = dialog.state.project.value
        assert p.migrated_from is not None and not is_v1_recipe(dialog.root / NAME)
        assert dialog.state.current_page_view.value == "analysis_tile"
        assert dialog.filters == []  # no evaluator filter: not a review copy
        await _settle(lambda: _toasts("success"), "the converted toast")
        assert _toasts("success") == [t("steps.project.v1_converted", name=NAME, reviewed=p.migrated_from.n_reviewed,
                                        alerts=p.migrated_from.n_alerts)]
        assert _toasts("warning") == []  # a clean recipe has no notes
        await _settle(lambda: dialog.dismissed == 1, "the dialog closed")

    run_rendered(lambda: dialog.render(), main)


def test_conversion_notes_are_shown_once_as_a_warning(dialog, monkeypatch):  # noqa: F811
    write_recipe(dialog.root / NAME, rows=[{"bounding_box": "not wkt"}, {}])
    _resolver(monkeypatch, lambda params: AOI_FC)

    async def main(rc):
        await _settle(lambda: NAME in (_listed(dialog) or {}), "the recipe listed", rc=rc)
        dialog.manage[-1]["on_select"](NAME)
        dialog.manage[-1]["on_open"]()
        await _settle(lambda: _toasts("warning"), "the notes toast")
        notes = dialog.state.project.value.migrated_from.notes
        assert notes and _toasts("warning") == ["\n".join(notes)]

    run_rendered(lambda: dialog.render(), main)


def test_failed_conversion_installs_nothing(dialog, monkeypatch):  # noqa: F811
    write_recipe(dialog.root / NAME)

    def broken(params):
        raise RuntimeError("EE is down")

    _resolver(monkeypatch, broken)

    async def main(rc):
        await _settle(lambda: NAME in (_listed(dialog) or {}), "the recipe listed", rc=rc)
        dialog.manage[-1]["on_select"](NAME)
        dialog.manage[-1]["on_open"]()
        await _settle(lambda: _toasts("error"), "the error toast")
        assert "was not changed" in _toasts("error")[0] and "EE is down" in _toasts("error")[0]
        assert dialog.state.project.value is None and is_v1_recipe(dialog.root / NAME)
        assert dialog.manage[-1]["converting"] is None

    run_rendered(lambda: dialog.render(), main)


def test_v2_open_unchanged(dialog, monkeypatch):  # noqa: F811
    io.save_project(pd.create_project("a"))
    _resolver(monkeypatch, lambda params: pytest.fail("a v2 project is never resolved"))

    async def main(rc):
        await _settle(lambda: "a" in (_listed(dialog) or {}), "a listed", rc=rc)
        _button(rc, t("steps.project.continue")).fire_event("click", None)
        await _settle(lambda: getattr(dialog.state.project.value, "name", None) == "a", "a opened")
        assert dialog.state.current_page_view.value == "aoi_tile"
        await _settle(lambda: _toasts("success"), "the opened toast")
        assert _toasts("success") == [t("steps.project.opened", name="a")]

    run_rendered(lambda: dialog.render(), main)


def test_an_unconverted_recipe_deletes_without_a_manifest(dialog, monkeypatch):  # noqa: F811
    write_recipe(dialog.root / NAME)

    async def main(rc):
        await _settle(lambda: NAME in (_listed(dialog) or {}), "the recipe listed", rc=rc)
        await _delete(rc, dialog, NAME)
        await _settle(lambda: _toasts("success"), "the delete finished")
        assert not (dialog.root / NAME).exists()

    run_rendered(lambda: dialog.render(), main)

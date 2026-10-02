"""The review form stores language-neutral statuses: the labels follow the
locale the widgets were built in, the stored value never does."""

from types import SimpleNamespace

import pytest
from pysepal.i18n import set_locale

from gui.i18n import t
from gui.tile.analysis import export as export_mod
from gui.tile.analysis.review_panel import REVIEW_CHOICES, build_review_panel

STATUSES = ["Confirmed", "False Positive", "Need revision"]


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


@pytest.mark.parametrize("locale", ["en", "es-ES", "fr"])
def test_the_select_holds_statuses_and_shows_labels(locale):
    set_locale(locale)
    widgets = SimpleNamespace()
    build_review_panel(widgets, None, None, None)  # it reads nothing but ``widgets``
    select = widgets.confirmation_select
    assert [i["value"] for i in select.items] == STATUSES
    assert [i["text"] for i in select.items] == [t(key) for _, key in REVIEW_CHOICES]
    assert select.v_model == "Confirmed"


def test_review_status():
    for status in STATUSES:
        assert export_mod.review_status(status) == status
    for bad in (None, "", "Yes", t("analysis_tile.questionarie.confirmation_yes"), ["Confirmed"], {"value": "Confirmed"}):
        with pytest.raises(export_mod.SaveRefused):
            export_mod.review_status(bad)

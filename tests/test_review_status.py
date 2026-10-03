"""The review form stores language-neutral statuses: the labels follow the
locale the widgets were built in, the stored value never does."""

from types import SimpleNamespace

import pytest
from pysepal.i18n import set_locale

from gui.i18n import t
from gui.scripts.set_context import SetContext
from gui.store.state_manager import AppState
from gui.tile.analysis import export as export_mod
from gui.tile.analysis.review_panel import REVIEW_CHOICES, build_review_panel, verdict_widgets
from gui.tile.analysis.state import AnalysisState

STATUSES = ["Confirmed", "False Positive", "Need revision"]


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


@pytest.mark.parametrize("locale", ["en", "es-ES", "fr"])
def test_the_buttons_hold_statuses_and_show_labels(locale):
    set_locale(locale)
    app_state = AppState()
    analysis = AnalysisState()
    card = build_review_panel(SimpleNamespace(), app_state, analysis, SetContext(app_state))
    buttons, *_ = verdict_widgets(card)
    assert list(buttons) == STATUSES
    labels = [b.children[1].children[0] for b in buttons.values()]
    assert labels == [t(key) for _, key in REVIEW_CHOICES]
    assert analysis.verdict.value is None     # no default answer is ever saved


def test_review_status():
    for status in STATUSES:
        assert export_mod.review_status(status) == status
    for bad in (None, "", "Yes", t("analysis_tile.verdict.true"), ["Confirmed"], {"value": "Confirmed"}):
        with pytest.raises(export_mod.SaveRefused):
            export_mod.review_status(bad)

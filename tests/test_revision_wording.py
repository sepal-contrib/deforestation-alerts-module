"""The 'needs revision' wording is one phrase in the verdict button, legend and line legend."""

import json
from pathlib import Path

import pytest

MESSAGES = Path(__file__).parent.parent / "deforestation_alerts" / "message"
LOCALES = sorted(p.name for p in MESSAGES.iterdir() if p.is_dir())


@pytest.mark.parametrize("locale", LOCALES)
def test_revision_wording_is_one_phrase(locale):
    ui = json.loads((MESSAGES / locale / "ui.json").read_text(encoding="utf-8"))
    legend = json.loads((MESSAGES / locale / "legend.json").read_text(encoding="utf-8"))
    button = ui["analysis_tile"]["verdict"]["revision"].lower()
    assert button in legend["legend"]["status"]["needs_revision"].lower()
    assert button in ui["analysis_tile"]["earlier"]["legend_lines"].lower()

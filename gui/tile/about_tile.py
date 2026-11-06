"""About tile as a Solara component (was the ui.ipynb TileAbout)."""

from pathlib import Path

import solara

from gui.i18n import t

# Resolve the about markdown relative to the repo root (Phase-2 docs/about/),
# so it works regardless of the process CWD. gui/tile/about_tile.py -> repo root
# is parents[2].
_ABOUT_DIR = Path(__file__).resolve().parents[2] / "docs" / "about"


@solara.component
def AboutTile():
    about_path = _ABOUT_DIR / t("about.pathname")
    # Wrap in a classed div so the dialog can justify the prose (see the
    # ``.about-content`` rule in solara_app.Page's Style) without affecting the
    # other (form) dialogs.
    with solara.Div(classes=["about-content"]):
        solara.Markdown(about_path.read_text())

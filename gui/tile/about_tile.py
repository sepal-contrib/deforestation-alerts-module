"""About tile as a Solara component (was the ui.ipynb TileAbout)."""

from pathlib import Path

import solara

from deforestation_alerts.message import cm

# Resolve the about markdown relative to the repo root (Phase-2 docs/about/),
# so it works regardless of the process CWD. gui/tile/about_tile.py -> repo root
# is parents[2].
_ABOUT_DIR = Path(__file__).resolve().parents[2] / "docs" / "about"


@solara.component
def AboutTile():
    about_path = _ABOUT_DIR / cm.about.pathname
    solara.Markdown(about_path.read_text())

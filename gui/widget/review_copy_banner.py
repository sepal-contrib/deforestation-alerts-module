"""The review-copy banner (spec §6.3), shown above every workflow step while
the open project is a review copy."""

import reacton.ipyvuetify as rv
import solara

from deforestation_alerts.project.review_copy import read_only
from gui.scripts.setup_guard import banner_text


@solara.component
def ReviewCopyBanner(app_state):
    p = app_state.project.value
    if not read_only(p):
        return
    # Inline on purpose: a persistent state description (as the AOI step's
    # locked hint), not the outcome of an action.
    rv.Alert(type="info", dense=True, text=True, icon="mdi-lock-outline", class_="ma-2 mb-0",
             children=[banner_text(p)])

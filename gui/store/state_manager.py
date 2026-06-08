"""Ephemeral, non-serialized UI/navigation state (D8). Serializable domain state
(recipe, alerts) stays in pysepal.model.Model subclasses; AppState only holds
references + transient UI flags as solara.reactive values.

Layer rule (spec §4.2): this module imports solara but NO views and NO GEE."""

import solara


class AppState:
    def __init__(self):
        # navigation
        self.current_page_view = solara.reactive("about_tile")
        # alert review
        self.actual_alert_id = solara.reactive(-1)
        self.alerts_gdf = solara.reactive(None)  # ref to the domain gdf
        # transient flags
        self.loading = solara.reactive(False)
        self.error_message = solara.reactive(None)
        self.recipe_name = solara.reactive("")

    @property
    def max_alert_id(self):
        gdf = self.alerts_gdf.value
        return (len(gdf) - 1) if gdf is not None else -1

    def go_to(self, idx):
        self.actual_alert_id.value = max(0, min(idx, self.max_alert_id))

    def go_next(self):
        self.go_to(self.actual_alert_id.value + 1)

    def go_prev(self):
        self.go_to(self.actual_alert_id.value - 1)

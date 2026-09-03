"""Ephemeral, non-serialized UI/navigation state (D8). Serializable domain state
(recipe, alerts) stays in pysepal.model.Model subclasses; AppState only holds
references + transient UI flags as solara.reactive values.

Layer rule (spec §4.2): this module imports solara but NO views and NO GEE."""

import solara


class AppState:
    def __init__(self):
        # navigation
        self.current_page_view = solara.reactive("about_tile")
        # The active *map* tile: the right-panel tile (AOI / Filter / Overview)
        # that owns the layers drawn on the single shared map. Distinct from
        # current_page_view (which mirrors every tile, popups included): this only
        # changes on a right-panel tile activation, so opening a popup over the map
        # leaves the underlying tile's content in place. The map-bearing tiles key
        # their draw/clear effect off this. Empty until a panel tile is activated
        # (the initial step is the About dialog). See gui/store/navigation.py:
        # active_map_view_for_step and the _on_step handler in solara_app.py.
        self.active_map_view = solara.reactive("")
        # Workflow position: index into gui.store.workflow_steps.STEPS. Owned by
        # the shell; set by the pipeline header (user clicks) and by the
        # navigation bridge (tiles' current_page_view writes).
        self.active_step = solara.reactive(0)
        # Which view the right panel shows: "workflow" (pipeline header + the
        # active step's controls) or a panel rail item's key ("overview").
        self.panel_view = solara.reactive("workflow")
        # Mirror of aoi_date_model.name, maintained by a traitlets observer
        # installed in Page(). The header cannot subscribe to a traitlets model,
        # so this reactive is what re-renders the strip when an AOI is chosen or
        # cleared. None when no AOI is selected.
        self.aoi_name = solara.reactive(None)
        # alert review
        self.actual_alert_id = solara.reactive(-1)
        self.alerts_gdf = solara.reactive(None)  # ref to the domain gdf
        # transient flags
        self.loading = solara.reactive(False)
        self.error_message = solara.reactive(None)
        self.recipe_name = solara.reactive("")
        # folder of the currently-loaded recipe (set by RecipeTile load); used by
        # the Overview download button to locate <folder>/alert_db.csv.
        self.recipe_folder_path = solara.reactive("")

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

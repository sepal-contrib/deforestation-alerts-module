"""AppState: ephemeral reactive UI/nav state (D8)."""

from gui.store.state_manager import AppState


def test_defaults():
    s = AppState()
    assert s.current_page_view.value == "about_tile"
    assert s.actual_alert_id.value == -1
    assert s.alerts_gdf.value is None
    assert s.loading.value is False
    # The active *map* tile is distinct from current_page_view: it tracks only the
    # right-panel tile that owns the shared map's layers, ignoring popups. No panel
    # tile is active at startup (the initial step is the About dialog).
    assert s.active_map_view.value == ""


def test_reactives_are_independent_per_instance():
    a, b = AppState(), AppState()
    a.current_page_view.value = "aoi_tile"
    assert b.current_page_view.value == "about_tile"


def test_navigation_helpers():
    s = AppState()
    s.alerts_gdf.value = list(range(5))  # stand-in: 5 alerts
    s.actual_alert_id.value = 0
    s.go_next()
    assert s.actual_alert_id.value == 1
    s.go_to(4)
    s.go_next()  # clamp at last
    assert s.actual_alert_id.value == 4


def test_workflow_position_defaults_to_the_first_step():
    s = AppState()
    assert s.active_step.value == 0
    s.active_step.set(2)
    assert s.active_step.value == 2


def test_panel_view_defaults_to_workflow():
    s = AppState()
    assert s.panel_view.value == "workflow"
    s.panel_view.set("overview")
    assert s.panel_view.value == "overview"


def test_aoi_name_mirror_defaults_to_none():
    s = AppState()
    assert s.aoi_name.value is None
    s.aoi_name.set("Canindeyú")
    assert s.aoi_name.value == "Canindeyú"


def test_navigation_signal_and_map_owner_are_unchanged():
    s = AppState()
    assert s.current_page_view.value == "about_tile"
    assert s.active_map_view.value == ""


# ---------------------------------------------------------------------------
# aoi_name_from_model + the trait contract the Page() mirror depends on.
#
# Regression (Task 10 review): the mirror observed ``feature_collection``, which
# is NOT a traitlet on AoiModel, so it never fired -- app_state.aoi_name stayed
# None for SHAPE/DRAW/POINTS/ASSET AOIs and for every recipe load, and the
# pipeline header showed AOI=EMPTY / Filter=LOCKED forever.
# ---------------------------------------------------------------------------
class _FakeAoi:
    """Positional stand-in for AoiModel's two relevant attributes."""

    def __init__(self, name=None, feature_collection=None):
        self.name = name
        self.feature_collection = feature_collection


def test_aoi_name_from_model_needs_geometry_and_a_name():
    from gui.store.state_manager import aoi_name_from_model

    assert aoi_name_from_model(_FakeAoi()) is None
    # a name with no geometry is a stale label, not a selected AOI
    assert aoi_name_from_model(_FakeAoi(name="myshapefile")) is None
    # geometry with no usable name is still "no AOI" for the header gates
    assert aoi_name_from_model(_FakeAoi(feature_collection=object())) is None
    assert aoi_name_from_model(_FakeAoi(name="", feature_collection=object())) is None
    assert (
        aoi_name_from_model(_FakeAoi(name="PRY_Canindeyu", feature_collection=object()))
        == "PRY_Canindeyu"
    )
    # the helper must not require the attributes to exist at all
    assert aoi_name_from_model(object()) is None


def test_only_object_set_and_name_can_notify_the_aoi_mirror():
    """The names Page() observes must be real traits (credential-free check).

    ``HasTraits.observe`` does not validate names: registering a non-trait name
    is accepted and then never fires, which is exactly how the original
    ``names=["feature_collection", "name"]`` mirror failed silently.
    """
    from pysepal.aoi.aoi_model import AoiModel

    traits = AoiModel.class_trait_names()
    assert "object_set" in traits
    assert "name" in traits
    assert "feature_collection" not in traits, (
        "feature_collection became a trait upstream -- revisit the aoi_name mirror"
    )


def _mirror(model):
    """Install the same handler Page()._mirror_aoi_name installs, on a plain list."""
    from gui.store.state_manager import aoi_name_from_model

    seen = []

    def _on_aoi(change=None):
        seen.append(aoi_name_from_model(model))

    model.observe(_on_aoi, names=["object_set", "name"])
    _on_aoi()
    return seen


def test_aoi_mirror_catches_both_loader_orderings(gee):
    """Drive a real AoiDateModel through both real assignment orderings.

    pysepal's loaders disagree on order: ``_from_admin`` assigns
    feature_collection then name, while _from_asset/_from_points/_from_vector/
    _from_geo_json assign name FIRST. Only the trailing ``object_set`` bump sees
    both. With the buggy ``names=["feature_collection", "name"]`` the SHAPE case
    below ends on None.
    """
    from deforestation_alerts.model.aoi_date_model import AoiDateModel

    # SHAPE/DRAW/POINTS/ASSET ordering: name, then geometry, then the bump
    m = AoiDateModel()
    seen = _mirror(m)
    m.name = "myshapefile"
    m.feature_collection = object()
    m.object_set += 1
    assert seen[-1] == "myshapefile"

    # ADMIN ordering: geometry, then name, then the bump
    m2 = AoiDateModel()
    seen2 = _mirror(m2)
    m2.feature_collection = object()
    m2.name = "PRY_Canindeyu"
    m2.object_set += 1
    assert seen2[-1] == "PRY_Canindeyu"

    # switching AOIs must not strand the previous label
    m2.name = None
    assert seen2[-1] is None


def test_recipe_load_notifies_the_aoi_mirror(gee):
    """import_from_dictionary bypasses set_object(), so it must bump object_set.

    Without the bump a recipe load leaves the header on AOI=EMPTY even for the
    ADMIN method, because ``name`` is written before the loader runs and
    traitlets suppresses the loader's identical re-assignment.
    """
    from deforestation_alerts.model.aoi_date_model import AoiDateModel

    m = AoiDateModel()
    seen = _mirror(m)
    before = m.object_set
    # No method => no _from_* dispatch (no extra EE call); stand in for the
    # geometry the loader would have produced.
    m.name = "PRY_Canindeyu"
    m.feature_collection = object()
    m.import_from_dictionary({"aoi_name": "PRY_Canindeyu"})
    assert m.object_set == before + 1, "recipe load must bump object_set"
    assert seen[-1] == "PRY_Canindeyu"

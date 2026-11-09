from datetime import datetime

from deforestation_alerts.project.models import Project
from deforestation_alerts.scripts.image_sources import (
    MENU,
    SOURCES,
    dated_optical,
    is_radar,
    enabled_entries,
    enabled_sources,
    entry_of,
)


def _project(**kw):
    return Project(name="d", created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1), **kw)


def test_the_existing_codes_and_names_do_not_move():
    codes = {n: s.code for n, s in SOURCES.items()}
    assert {"Sentinel 2": "s", "Landsat": "l", "Planet NICFI": "p"}.items() <= codes.items()
    assert len(set(codes.values())) == len(codes)


def test_the_menu_lists_every_source_once():
    members = [m for e in MENU for m in e.members]
    assert len(members) == len(set(members))
    assert set(members) == set(SOURCES)


def test_a_project_without_image_sources_has_the_defaults():
    assert enabled_entries(_project(image_sources=None)) == {"Sentinel 2", "Landsat"}
    assert enabled_entries(None) == {"Sentinel 2", "Landsat"}


def test_unknown_menu_keys_are_ignored():
    assert enabled_entries(_project(image_sources=["Landsat", "MODIS"])) == {"Landsat"}


def test_sentinel_1_is_two_passes_behind_one_entry():
    p = _project(image_sources=["Sentinel 1"])
    assert enabled_sources(p) == {"Sentinel 1 ascending", "Sentinel 1 descending"}


def test_only_dated_optical_sources_feed_the_auto_pick():
    assert dated_optical("Sentinel 2") and dated_optical("Landsat")
    for n in ("Planet NICFI", "Sentinel 1 ascending", "Sentinel 1 descending", "MODIS"):
        assert not dated_optical(n)


def test_only_sentinel_1_is_radar_and_an_unknown_name_is_not():
    assert is_radar("Sentinel 1 ascending") and is_radar("Sentinel 1 descending")
    for n in ("Sentinel 2", "Landsat", "Planet NICFI", "MODIS"):
        assert not is_radar(n)


def test_a_project_round_trips_its_image_sources():
    p = _project(image_sources=["Sentinel 1"])
    assert Project.model_validate(p.model_dump(mode="json")).image_sources == ["Sentinel 1"]


def test_each_sources_entry_field_matches_the_menu_entry_holding_it():
    for name, source in SOURCES.items():
        assert source.entry == entry_of(name).key, name

"""S1 file tiles (spec D6): a disabled tile until a file is set, then a
download link with its size."""

import pytest

from gui.tile.analysis.file_tile import FileTile, human_size


def _strings(w):
    out = []
    for c in getattr(w, "children", None) or []:
        out.extend([c] if isinstance(c, str) else _strings(c))
    return out


def test_a_tile_without_a_file_is_disabled():
    tile = FileTile("zip", "Alert data", "GeoPackage + GeoTIFF")
    assert tile.disabled and "Alert data" in _strings(tile) and "ZIP" in _strings(tile)
    assert tile.kind == "zip" and tile.size_text == ""


def test_set_file_links_it_and_shows_its_size(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    f = tmp_path / "set" / "alert_3.zip"
    f.parent.mkdir()
    f.write_bytes(b"x" * 2048)
    tile = FileTile("zip", "Alert data", "GeoPackage + GeoTIFF")
    tile.set_file(str(f))
    assert not tile.disabled and tile.attributes["download"] == "alert_3.zip"
    # The sandbox file route of the $HOME-relative path (pysepal's set_url).
    assert tile.href.endswith("/set/alert_3.zip")
    assert tile.size_text == "2 KB" and "2 KB" in " ".join(_strings(tile))
    tile.set_file(None)
    assert tile.disabled and tile.size_text == ""
    assert tile.attributes["download"] is None
    assert "2 KB" not in " ".join(_strings(tile))  # the detail is back to its own text


def test_a_file_gone_from_disk_still_links_without_a_size(tmp_path, monkeypatch):
    """The size is a courtesy: a file that cannot be stat'ed is still offered."""
    monkeypatch.setenv("HOME", str(tmp_path))
    tile = FileTile("docx", "Alert report", "Word")
    tile.set_file(str(tmp_path / "set" / "alert_3.docx"))
    assert not tile.disabled and tile.size_text == ""
    assert "Word" in _strings(tile)


def test_a_path_outside_home_raises_and_offers_nothing(tmp_path, monkeypatch):
    """``home_relative`` refuses a path the sandbox cannot serve; the export
    worker turns that into a FAILED task, so the tile must not be left live."""
    import pytest

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    tile = FileTile("zip", "Alert data", "GeoPackage + GeoTIFF")
    with pytest.raises(ValueError):
        tile.set_file("/elsewhere/alert_3.zip")
    assert tile.disabled and tile.size_text == ""


def test_human_size():
    assert human_size(512) == "512 B" and human_size(4_400_000) == "4.2 MB"
    assert human_size(2048) == "2 KB" and human_size(0) == "0 B"


def test_a_size_that_rounds_to_1024_reads_in_the_next_unit():
    """Never "1024 KB" or "1024.0 MB": the next unit up, as people read it."""
    assert human_size(1_048_575) == "1.0 MB"          # 1023.999 KB
    assert human_size(1024 * 1024 * 1024 - 1) == "1.0 GB"  # 1023.99 MB
    assert human_size(1023) == "1023 B" and human_size(1024) == "1 KB"
    assert human_size(1023 * 1024) == "1023 KB"


def test_the_tile_is_the_s1_row_not_a_primary_button():
    """Mockup S1: a neutral, never-underlined row in the theme's text colour;
    only the badge and the download icon carry the primary colour."""
    import ipyvuetify as v

    tile = FileTile("zip", "Alert data", "GeoPackage + GeoTIFF")
    assert tile.color is None
    assert "text-decoration:none" in tile.style_ and "border-color:rgba(128,128,128" in tile.style_
    badge, text, icon = tile.children
    assert "primary--text" in badge.class_ and badge.children[0].class_ == "primary"
    assert isinstance(icon, v.Icon) and "primary--text" in icon.class_
    title, detail = text.children
    assert "font-weight:600" in title.style_ and "primary" not in (title.class_ or "")
    assert "opacity:0.6" in detail.style_


def test_set_action_makes_a_tile_without_a_file_a_plain_button(tmp_path, monkeypatch):
    """The GIF tile's second face: no link (a click must reach the handler,
    not open "#"), enabled or not, its detail saying why; a file wins."""
    monkeypatch.setenv("HOME", str(tmp_path))
    tile = FileTile("gif", "Timelapse", "Animated GIF")
    tile.set_action(True, "click to create it")
    assert tile.href is None and not tile.disabled and "click to create it" in _strings(tile)
    assert "mdi-filmstrip" in _strings(tile)
    tile.set_action(False, "save first")
    assert tile.disabled and "save first" in _strings(tile)
    f = tmp_path / "alert.gif"
    f.write_bytes(b"GIF89a")
    tile.set_file(str(f))
    tile.set_action(False, "save first")  # ignored: the tile links its file
    assert not tile.disabled and tile.path == str(f) and "mdi-download" in _strings(tile)
    tile.set_file(None)
    assert tile.path is None and tile.disabled and "mdi-download" in _strings(tile)


def test_set_file_takes_a_new_detail_and_a_warning(tmp_path, monkeypatch):
    """The Overview's export tiles: the line under the title says what the file
    holds and when it was written, and an out-of-date file leads with a warning."""
    monkeypatch.setenv("HOME", str(tmp_path))
    f = tmp_path / "set" / "a.gpkg"
    f.parent.mkdir()
    f.write_bytes(b"x" * 2048)
    tile = FileTile("gpkg", "GeoPackage", "3 polygons")
    tile.set_file(str(f), detail="3 polygons · 2026-10-15 14:02", warning="Reviews changed since")
    text = " ".join(_strings(tile))
    assert "Reviews changed since" in text and "3 polygons · 2026-10-15 14:02 · 2 KB" in text
    assert tile.warning == "Reviews changed since"
    tile.set_file(str(f))  # no warning: the note goes, the new detail stays
    text = " ".join(_strings(tile))
    assert "Reviews changed since" not in text and tile.warning is None
    assert "3 polygons · 2026-10-15 14:02 · 2 KB" in text


def test_set_title_renames_the_tile():
    tile = FileTile("csv", "CSV", "")
    tile.set_title("CSV table")
    assert "CSV table" in _strings(tile) and "CSV" in _strings(tile)


def test_set_detail_rewrites_the_line_of_an_empty_tile():
    tile = FileTile("zip", "Alert data", "GeoPackage")
    tile.set_detail("GeoPackage (fr)")
    assert tile._meta.children == ["GeoPackage (fr)"]


def test_set_detail_leaves_an_action_tile_its_action():
    tile = FileTile("gif", "Timelapse", "GIF")
    tile.set_action(False, "Save first")
    tile.set_detail("GIF (fr)")
    assert tile._meta.children == ["Save first"]
    tile.set_file(None)
    assert tile._meta.children == ["GIF (fr)"]


def test_set_detail_keeps_a_linked_file_and_its_size(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    f = tmp_path / "a.zip"
    f.write_bytes(b"x" * 2048)
    tile = FileTile("zip", "Alert data", "GeoPackage").set_file(str(f))
    href = tile.href
    tile.set_detail("GeoPackage (fr)")
    assert tile.path == str(f) and tile.href == href
    assert tile._meta.children == [f"GeoPackage (fr) · {tile.size_text}"]


def test_a_refused_file_leaves_an_action_tile_its_action(monkeypatch):
    """``set_file`` changes nothing before the path is accepted, the action
    text included: the next relabel must not uncover the plain detail."""
    from deforestation_alerts.scripts import local_files

    def _refuse(path):
        raise ValueError("outside the sandbox")

    monkeypatch.setattr(local_files, "home_relative", _refuse)
    tile = FileTile("gif", "Timelapse", "GIF")
    tile.set_action(False, "Save first")
    with pytest.raises(ValueError):
        tile.set_file("/etc/passwd")
    tile.set_detail("GIF (fr)")
    assert tile.path is None and tile._meta.children == ["Save first"]


def test_relabel_action_rewrites_only_the_action_text():
    """A running GIF: the tile is held (disabled, loading); only its words
    change."""
    tile = FileTile("gif", "Timelapse", "GIF")
    tile.set_action(True, "Make the GIF")
    tile.disabled, tile.loading = True, True
    icon = tile._icon.children
    tile.relabel_action("Créer le GIF")
    assert tile._meta.children == ["Créer le GIF"]
    assert (tile.disabled, tile.loading, tile.href, tile._icon.children) == (True, True, None, icon)


def test_relabel_action_leaves_a_tile_that_is_not_an_action():
    tile = FileTile("zip", "Alert data", "GeoPackage")
    tile.relabel_action("Créer")
    assert tile._meta.children == ["GeoPackage"]

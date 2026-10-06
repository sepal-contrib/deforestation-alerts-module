"""S1 file tiles (spec D6): a disabled tile until a file is set, then a
download link with its size; the GIF tile is a placeholder."""

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

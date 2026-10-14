"""Which project folders a background task is writing into
(``gui/store/project_writers.py``): the delete confirmation's guard."""

from gui.store.project_writers import is_writing, writing


def test_project_writers_track_nested_writes():
    assert not is_writing("recipe-x")
    with writing("recipe-x"):
        assert is_writing("recipe-x")
        with writing("recipe-x"):
            assert is_writing("recipe-x")
        assert is_writing("recipe-x")
    assert not is_writing("recipe-x")


def test_writing_releases_on_error():
    try:
        with writing("recipe-y"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert not is_writing("recipe-y")

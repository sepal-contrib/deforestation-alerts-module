"""CreationDialog contract guards (ported from the spatial_risk scaffold)."""

import inspect

from gui.widget import creation_dialog


def test_creation_dialog_wiring():
    src = inspect.getsource(creation_dialog)
    # Replace flow must delegate to the shared ConfirmDialog — never a second
    # hand-rolled confirmation.
    assert "ConfirmDialog(" in src
    # eager=True keeps the form's widget views alive across open/close.
    assert "eager=True" in src
    # The Create action carries the canonical plus icon.
    assert "mdi-plus" in src


def test_project_writers_track_nested_writes():
    from gui.store.project_writers import is_writing, writing

    assert not is_writing("recipe-x")
    with writing("recipe-x"):
        assert is_writing("recipe-x")
        with writing("recipe-x"):
            assert is_writing("recipe-x")
        assert is_writing("recipe-x")
    assert not is_writing("recipe-x")


def test_writing_releases_on_error():
    from gui.store.project_writers import is_writing, writing

    try:
        with writing("recipe-y"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert not is_writing("recipe-y")

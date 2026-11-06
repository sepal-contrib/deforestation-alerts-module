"""The report template of a project (spec §10): picked in the Analysis export
card, stored as an absolute .docx path, used by every report of the project."""

from datetime import datetime
from pathlib import Path

import pytest
import reacton
import solara
from pysepal.sepalwidgets.file_input import FileInput
from pysepal.solara.notifications import NotificationProvider
from pysepal.solara.notifications.bus import get_current_bus

from deforestation_alerts.project import io
from deforestation_alerts.project.models import Project
from gui.scripts.error_text import error_text
from gui.store.autosave import install_autosave
from gui.store.state_manager import AppState
from gui.tile.analysis import alert_files
from gui.widget import report_template as rt


def _docx(path: Path) -> Path:
    path.write_bytes(Path(alert_files.DEFAULT_REPORT_TEMPLATE).read_bytes())
    return path


def test_template_value(tmp_path):
    good = _docx(tmp_path / "Mine.DOCX")
    assert rt.template_value("") is None and rt.template_value(None) is None
    assert rt.template_value(str(good)) == str(good.absolute())
    with pytest.raises(ValueError, match="report_template_bad"):
        rt.template_value(str(tmp_path / "gone.docx"))
    (tmp_path / "notes.txt").write_text("x")
    with pytest.raises(ValueError, match="report_template_bad"):
        rt.template_value(str(tmp_path / "notes.txt"))


def test_report_template_path(tmp_path):
    assert alert_files.report_template_path(None) == Path(alert_files.DEFAULT_REPORT_TEMPLATE)
    assert alert_files.report_template_path("") == Path(alert_files.DEFAULT_REPORT_TEMPLATE)
    mine = _docx(tmp_path / "mine.docx")
    assert alert_files.report_template_path(str(mine)) == mine
    with pytest.raises(alert_files.ReportTemplateMissing) as err:
        alert_files.report_template_path(str(tmp_path / "gone.docx"))
    assert err.value.values == {"path": str(tmp_path / "gone.docx")}
    assert err.value.key == "analysis_tile.export_labels.report_template_missing"
    # The download's failure toast (tracked_job's default) reads the keyed message, with the path.
    toast = error_text(err.value)
    assert "gone.docx" in toast and toast != err.value.key
    assert str(err.value) == f"report template not found: {tmp_path / 'gone.docx'}"


@solara.component
def _Harness(state):
    NotificationProvider()
    rt.ReportTemplateField(app_state=state)


@pytest.fixture
def field(data_root):
    state = AppState()
    unsubscribe = install_autosave(state)
    now = datetime.now()
    state.new_project_state(Project(name="r", created_at=now, updated_at=now))
    box, rc = reacton.render(_Harness(state=state), handle_error=False)
    yield state, rc
    rc.close()
    unsubscribe()


def _toasts(kind):
    return [x.message for x in get_current_bus().toasts.value if x.type.value == kind]


def test_a_pick_is_saved_in_the_project_and_clearing_returns_to_the_default(field, data_root):
    state, rc = field
    mine = _docx(data_root / "mine.docx")
    rc.find(FileInput).widget.v_model = str(mine)
    assert state.project.value.report_template == str(mine.absolute())
    assert io.load_project("r").report_template == str(mine.absolute())  # auto-saved
    rc.find(FileInput).widget.v_model = ""
    assert state.project.value.report_template is None


def test_a_bad_pick_is_refused_with_a_toast(field, data_root):
    state, rc = field
    rc.find(FileInput).widget.v_model = str(data_root / "gone.docx")
    assert state.project.value.report_template is None
    assert any("gone.docx" in m for m in _toasts("error"))


def test_a_refused_pick_resets_the_field(field, data_root):
    """The field shows what the project holds, never the refused file."""
    state, rc = field
    rc.find(FileInput).widget.v_model = str(data_root / "gone.docx")
    assert rc.find(FileInput).widget.v_model == ""
    mine = _docx(data_root / "mine.docx")
    rc.find(FileInput).widget.v_model = str(mine)
    (data_root / "notes.txt").write_text("x")
    rc.find(FileInput).widget.v_model = str(data_root / "notes.txt")
    assert state.project.value.report_template == str(mine.absolute())
    assert rc.find(FileInput).widget.v_model == str(mine.absolute())
    # The same refused file picked twice in a row is refused (and reset) twice.
    rc.find(FileInput).widget.v_model = str(data_root / "notes.txt")
    assert rc.find(FileInput).widget.v_model == str(mine.absolute())
    assert state.project.value.report_template == str(mine.absolute())


def test_the_field_follows_a_project_switch(field, data_root):
    state, rc = field
    mine = _docx(data_root / "mine.docx")
    now = datetime.now()
    state.new_project_state(Project(name="s", created_at=now, updated_at=now, report_template=str(mine)))
    assert rc.find(FileInput).widget.v_model == str(mine)


def test_template_state(tmp_path):
    assert rt.template_state(None) == "default" and rt.template_state("") == "default"
    mine = _docx(tmp_path / "mine.docx")
    assert rt.template_state(str(mine)) == "custom"
    assert rt.template_state(str(tmp_path / "gone.docx")) == "missing"

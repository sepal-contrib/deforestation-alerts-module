"""The report template picker of the Analysis export card (spec §10).

A project's reports are built from ``project.report_template`` (an absolute
path to a ``.docx`` in the user's SEPAL folder), or from the app's default
template when it is empty. The field is rendered into the raw export card with
``render_detached``; it resolves its notifier when a file is picked, so it also
renders where no ``NotificationProvider`` is mounted.
"""

from pathlib import Path
from typing import Optional

import solara
from pysepal.solara.components.inputs import FileInputComponent

from deforestation_alerts.errors import ProjectRuleError
from gui.i18n import t
from gui.scripts.error_text import error_text
from gui.scripts.notify_bridge import current_notifier
from gui.widget.text_style import MUTED

EXTENSIONS = [".docx"]


def template_value(path) -> Optional[str]:
    """What the project stores for a pick: None (the default template) or the
    absolute path of an existing ``.docx`` file (spec §11: absolute paths).
    Anything else raises ``ProjectRuleError`` (``report_template_bad``)."""
    if not path:
        return None
    picked = Path(path).expanduser().absolute()
    if picked.suffix.lower() != ".docx" or not picked.is_file():
        raise ProjectRuleError(key="analysis_tile.export_labels.report_template_bad", path=str(path))
    return str(picked)


@solara.component
def ReportTemplateField(app_state):
    # Bumped by every refused pick: ``FileInputComponent`` keeps the picked
    # path in its own state while its ``value`` prop is unchanged, so a refused
    # file would stay on screen. A new key remounts it on what the project holds.
    # The key sits on the Column: reacton compares a child element without its
    # key, so a re-keyed child of an otherwise unchanged Column is not re-rendered.
    refusals, set_refusals = solara.use_state(0)
    p = app_state.project.value
    if p is None:
        solara.Text(t("analysis_tile.export_labels.report_template_no_project"), style=MUTED)
        return
    name, current = p.name, p.report_template

    def _pick(path):
        try:
            value = template_value(path)
        except ValueError as exc:
            current_notifier().error(error_text(exc))
            set_refusals(lambda n: n + 1)
            return
        if value != current:
            app_state.update_project(name, lambda live: setattr(live, "report_template", value))

    with solara.Column(style="gap:2px;").key(f"{name}:{current}:{refusals}"):
        FileInputComponent(extensions=EXTENSIONS, value=current or "", on_value=_pick,
                           label=t("analysis_tile.export_labels.report_template_label"))
        solara.Text(t("analysis_tile.export_labels.report_template_hint"), style=MUTED + "font-size:0.8rem;")

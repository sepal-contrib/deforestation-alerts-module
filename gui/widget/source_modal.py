"""Add Source modal (spec §6.2). Slice 1 offers the predefined catalogue only;
slice 3 adds the custom branch (Earth Engine asset or GeoTIFF, RADD or CCDC)."""

import reacton.ipyvuetify as rv
import solara

from deforestation_alerts.engine import catalogue
from gui.i18n import t


def available_predefined(project) -> list:
    return [k for k in catalogue.KEYS if project is None or k not in project.sources]


@solara.component
def SourceModal(open_, project, on_add):
    # ``project`` is the reactive (ruling #16), not its value: reading
    # ``.value`` here, not at the call site, keeps this component subscribed
    # to every source-list change instead of freezing on the props it was
    # first rendered with.
    choices = available_predefined(project.value)
    key, set_key = solara.use_state(choices[0] if choices else None)
    if key not in choices:
        key = choices[0] if choices else None

    # One Add per opening (ruling #65): without this, a fast double-click
    # fires ``on_add`` twice with the same key, and the second call raises
    # "already a source" before the dialog has closed.
    submitted = solara.use_ref(False)

    def _rearm():
        if open_.value:
            submitted.current = False

    solara.use_effect(_rearm, [open_.value])

    def _add():
        if submitted.current:
            return
        submitted.current = True
        if key is not None:
            on_add(key)
        open_.set(False)

    dialog = rv.Dialog(v_model=open_.value, on_v_model=open_.set, max_width="480px", eager=True, persistent=True)
    # ``persistent`` blocks ESC too; restore it as a plain cancel (ruling #65).
    # rv.use_event is a hook -- call it unconditionally.
    rv.use_event(dialog, "keydown.esc", lambda *_: open_.set(False))
    with dialog:
        with rv.Card():
            with rv.CardTitle():
                solara.Text(t("steps.alerts.add_title"))
            with rv.CardText():
                if choices:
                    rv.Select(
                        label=t("steps.alerts.predefined_label"),
                        items=[{"text": t(catalogue.LABEL_KEYS[k]), "value": k} for k in choices],
                        v_model=key, on_v_model=set_key, dense=True, outlined=True,
                    )
                else:
                    solara.Text(t("steps.alerts.all_added"), classes=["text--secondary"])
            with rv.CardActions(style_="justify-content:flex-end;gap:8px;"):
                solara.Button(t("common.cancel"), text=True, small=True, on_click=lambda: open_.set(False))
                solara.Button(t("steps.alerts.add"), color="primary", small=True, icon_name="mdi-plus",
                              disabled=key is None, on_click=_add)

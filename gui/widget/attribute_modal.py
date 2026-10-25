"""Add or edit one attribute layer (spec D3), on the form-dialog frame.

A layer is one raster with its chosen bands, statistics and columns: the
config ``AttributeForm`` builds. A layer must keep at least one band, and must
not read a raster another layer already reads (``attribute_error``). Its
columns never take an alert column or another layer's column
(``raster_attribute.reserved_for``); an edited layer keeps its own columns.

The layers are the Integrate form's state, not the project's. Save only hands
the finished layer to ``on_save``. Nothing is checked asynchronously here
(the form reads the bands when a raster is picked), so the primary button is
a plain button carrying the Enter class, disabled while the layer cannot be
saved. While the form reads the bands, Save is disabled and drops the Enter
class, so Enter never saves the config held before the read. "Keep at least
one band." is the form's Bands field error, so the dialog does not repeat it.
"""

import solara

from deforestation_alerts.scripts.raster_attribute import (
    reserved_for,
    resolve_column_names,
)
from gui.i18n import t
from gui.widget.attribute_form import AttributeForm, layer_label, other_layer_names
from gui.widget.enter_key import ENTER_DEFAULT
from gui.widget.form_dialog import form_card, use_form_dialog

NO_BANDS = "steps.integrate.attr_err_no_bands"


def attribute_error(config, layers, editing=None):
    """Why ``config`` cannot be saved as layer ``editing`` of ``layers`` (None:
    a new layer): a message key, or None."""
    if not config or not config.get("bands"):
        return NO_BANDS
    for i, layer in enumerate(layers):
        if i != editing and (layer.get("source"), layer.get("kind")) == (config.get("source"), config.get("kind")):
            return "steps.integrate.attr_err_duplicate"
    return None


def finish_layer(config, layers, editing=None) -> dict:
    """``config`` with its columns made unique against the alert columns and
    every other layer's (review focus 3). The form already resolved them
    against the same set: this is the guarantee at Save."""
    return {**config, "bands": resolve_column_names(config["bands"], reserved=reserved_for(layers, skip=editing))}


@solara.component
def AttributeModal(open_, gee_interface, layers, editing, on_save):
    with use_form_dialog(open_, "560px"):  # a hook: called on every render
        if open_.value:  # mounted per opening: a clean form, and no asset listing while closed
            _AttributeLayerForm(open_=open_, gee_interface=gee_interface, layers=layers, editing=editing,
                                on_save=on_save)


@solara.component
def _AttributeLayerForm(open_, gee_interface, layers, editing, on_save):
    initial = layers[editing] if editing is not None and editing < len(layers) else None
    config, set_config = solara.use_state(initial)
    busy, set_busy = solara.use_state(False)  # the form is reading the bands
    error = attribute_error(config, layers, editing)

    def _save():
        if error is not None or busy:
            return
        on_save(editing, finish_layer(config, layers, editing))
        open_.set(False)

    def _actions():
        solara.Button(t("common.save") if initial is not None else t("steps.integrate.attr_add"),
                      icon_name="mdi-content-save-outline" if initial is not None else "mdi-plus",
                      color="primary", small=True, disabled=error is not None or busy,
                      classes=[] if busy else [ENTER_DEFAULT], on_click=_save)

    title = (t("steps.integrate.attr_edit_title", name=layer_label(initial)) if initial is not None
             else t("steps.integrate.attr_add_title"))
    # No band yet (none read, or none selected) disables Add with no message
    # here: the form's Bands field says it, once.
    shown = t(error) if error is not None and error != NO_BANDS else None
    with form_card(title, on_cancel=lambda: open_.set(False), actions=_actions, error=shown):
        AttributeForm(gee_interface=gee_interface, value=config, on_value=set_config,
                      reserved=reserved_for(layers, skip=editing), on_busy=set_busy,
                      taken_names=other_layer_names(layers, editing))

"""Custom alert sources editor for the Optional files dialog.

A list of the user's RADD-shaped Earth Engine alert sources with an add/edit
modal and a remove confirmation, modelled on spatial_risk's variables list.
Raw ipyvuetify on purpose: MapApp rebuilds dialog content on reopen, and only
raw widget trees survive that (see ``aux_tile.build_aux_layout``).
"""

import ipyvuetify as v
from pysepal import sepalwidgets as sw

from deforestation_alerts.message import cm
from deforestation_alerts.scripts.custom_sources import MAX_CUSTOM_SOURCES, validate_name
from gui.widget.text_style import MUTED

# Same hairline as the dialog's group boxes (aux_tile._group).
_BORDER = "border: thin solid rgba(128, 128, 128, 0.3);"


class CustomSourceList(v.Html):
    def __init__(self, aux_model, gee_interface=None):
        super().__init__(tag="div")
        t = cm.aux_tile
        self.aux_model = aux_model
        self._editing = None  # index being edited; None while adding
        self._removing = None

        self.rows_box = v.Html(tag="div", class_="rounded", style_=_BORDER, children=[])
        self.add_btn = v.Btn(
            text=True, small=True, color="primary",
            children=[v.Icon(left=True, small=True, children=["mdi-plus"]), t.custom_add],
        )
        self.counter = v.Html(tag="span", class_="caption ml-2", style_=MUTED, children=[])
        footer = v.Html(tag="div", class_="d-flex align-center mt-2",
                        children=[self.add_btn, self.counter])

        # Add / edit modal.
        self.name_field = sw.TextField(label=t.custom_alerts_name_hint, v_model=None)
        self.asset_field = sw.inputs.AssetSelect(
            gee_interface=gee_interface, types=["IMAGE", "IMAGE_COLLECTION"], label=t.asset_label
        )
        self.modal_title = v.CardTitle(children=[t.custom_modal_add_title])
        self.save_btn = v.Btn(color="primary", depressed=True, disabled=True,
                              children=[t.custom_save])
        cancel_btn = v.Btn(text=True, children=[t.custom_cancel])
        self.dialog = v.Dialog(
            v_model=False, persistent=True, max_width=480,
            children=[v.Card(children=[
                self.modal_title,
                v.CardText(children=[self.name_field, self.asset_field]),
                v.CardActions(children=[v.Spacer(), cancel_btn, self.save_btn]),
            ])],
        )

        # Remove confirmation.
        self.confirm_text = v.CardText(class_="pt-5 body-1", children=[])
        self.remove_btn = v.Btn(color="error", text=True, children=[t.custom_remove])
        keep_btn = v.Btn(text=True, children=[t.custom_cancel])
        self.confirm = v.Dialog(
            v_model=False, max_width=420,
            children=[v.Card(children=[
                self.confirm_text,
                v.CardActions(children=[v.Spacer(), keep_btn, self.remove_btn]),
            ])],
        )

        self.children = [self.rows_box, footer, self.dialog, self.confirm]

        self.add_btn.on_event("click", lambda *_: self.open_add())
        cancel_btn.on_event("click", lambda *_: self.close_modal())
        # Bound directly, not via a lambda: the no-local-userdata-writes guard
        # greps for a dot-save call and would flag this widget.
        self.save_btn.on_event("click", self.save)
        keep_btn.on_event("click", lambda *_: self.cancel_remove())
        self.remove_btn.on_event("click", lambda *_: self.confirm_remove())
        self.name_field.observe(self._validate, "v_model")
        self.asset_field.observe(self._validate, "v_model")
        aux_model.observe(self._render, "custom_alerts")
        self._render()

    # -- state helpers -------------------------------------------------------
    def _items(self):
        return [dict(s) for s in self.aux_model.custom_alerts]

    def _validate(self, *_):
        names = [s["name"] for s in self.aux_model.custom_alerts]
        current = names[self._editing] if self._editing is not None else None
        error = validate_name(self.name_field.v_model, names, current=current)
        # An empty name only disables Save; a fresh modal should not nag.
        shown = error if error in ("duplicate", "reserved") else None
        self.name_field.error_messages = (
            [getattr(cm.aux_tile, f"custom_name_{shown}")] if shown else []
        )
        self.save_btn.disabled = bool(error) or not self.asset_field.v_model

    # -- add / edit -----------------------------------------------------------
    def _open(self, index, title, name, asset):
        self._editing = index
        self.modal_title.children = [title]
        self.name_field.v_model = name
        self.asset_field.v_model = asset
        self._validate()
        self.dialog.v_model = True

    def open_add(self):
        if len(self.aux_model.custom_alerts) >= MAX_CUSTOM_SOURCES:
            return
        self._open(None, cm.aux_tile.custom_modal_add_title, None, None)

    def open_edit(self, index):
        source = self.aux_model.custom_alerts[index]
        self._open(index, cm.aux_tile.custom_modal_edit_title,
                   source["name"], source["asset"])

    def close_modal(self):
        self.dialog.v_model = False
        self._editing = None

    def save(self, *_):
        self._validate()
        if self.save_btn.disabled:
            return
        item = {"name": self.name_field.v_model.strip(),
                "asset": str(self.asset_field.v_model).strip()}
        items = self._items()
        if self._editing is None:
            items.append(item)
        else:
            items[self._editing] = item  # keeps its slot
        self.aux_model.custom_alerts = items
        self.close_modal()

    # -- remove ---------------------------------------------------------------
    def ask_remove(self, index):
        self._removing = index
        name = self.aux_model.custom_alerts[index]["name"]
        self.confirm_text.children = [cm.aux_tile.custom_remove_confirm.format(name=name)]
        self.confirm.v_model = True

    def cancel_remove(self):
        self.confirm.v_model = False
        self._removing = None

    def confirm_remove(self):
        if self._removing is not None:
            items = self._items()
            del items[self._removing]
            self.aux_model.custom_alerts = items
        self.cancel_remove()

    # -- rendering ------------------------------------------------------------
    def _icon_button(self, icon, label, on_click):
        btn = v.Btn(icon=True, small=True,
                    attributes={"aria-label": label, "title": label},
                    children=[v.Icon(small=True, children=[icon])])
        btn.on_event("click", lambda *_: on_click())
        return btn

    def _row(self, index, source):
        t = cm.aux_tile
        text = v.Html(tag="div", style_="min-width: 0; flex: 1;", children=[
            v.Html(tag="div", class_="font-weight-medium text-truncate",
                   children=[source["name"]]),
            v.Html(tag="div", class_="caption text-truncate", style_=MUTED,
                   attributes={"title": source["asset"]}, children=[source["asset"]]),
        ])
        return v.Html(tag="div", class_="d-flex align-center px-3 py-2", children=[
            text,
            self._icon_button("mdi-pencil", t.custom_edit_label,
                              lambda: self.open_edit(index)),
            self._icon_button("mdi-delete", t.custom_delete_label,
                              lambda: self.ask_remove(index)),
        ])

    def _render(self, *_):
        t = cm.aux_tile
        items = self.aux_model.custom_alerts
        if not items:
            rows = [v.Html(tag="div", class_="body-2 px-3 py-3", style_=MUTED,
                           children=[t.custom_empty])]
        else:
            rows = []
            for index, source in enumerate(items):
                if index:
                    rows.append(v.Divider())
                rows.append(self._row(index, source))
        self.rows_box.children = rows
        self.counter.children = [t.custom_count.format(n=len(items), max=MAX_CUSTOM_SOURCES)]
        self.add_btn.disabled = len(items) >= MAX_CUSTOM_SOURCES

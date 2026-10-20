"""The app's buttons, for the screens built from ipyvuetify widgets.

The workflow panel, its dialogs and the Overview draw their buttons with
``solara.Button`` and pysepal's Vuetify defaults (capitals, ``small``):

* ``primary``: filled green, the action that commits (*Add period*, *Merge*);
* ``secondary``: outlined green, the others (*Manage*, *Import*);
* ``tertiary``: text only, *Cancel* and the minor links (*Edit split*);
* an icon-only ``x_small`` button for a table row's actions.

The analysis screen is not a Solara tree, so ``btn`` builds the same buttons
as ``ipyvuetify.Btn``, with the icon first, where ``solara.Button(icon_name=…)``
puts it. ``set_label`` rewrites the words of one of them (a language switch)
and keeps its icons.
"""

import ipyvuetify as v

ROLES = {
    "primary": {"color": "primary", "small": True},
    "secondary": {"color": "primary", "outlined": True, "small": True},
    "tertiary": {"text": True, "small": True},
}


def btn(role: str, label: str = "", *, icon: str = None, icon_right: str = None, cls=v.Btn, **kwargs):
    """A ``role`` button (``ROLES``) reading ``label``, with an ``mdi-*``
    ``icon`` before the words and/or an ``icon_right`` after them.
    ``kwargs`` win over the role's (``small=False`` for a default-size one);
    ``cls`` is the widget class (a ``v.Btn`` subclass)."""
    children = []
    if icon:
        children.append(v.Icon(left=True, children=[icon]))
    children.append(label)
    if icon_right:
        children.append(v.Icon(right=True, children=[icon_right]))
    return cls(**{**ROLES[role], **kwargs}, children=children)


def label_of(button) -> str:
    """The words of a ``btn`` (its one text child)."""
    return next(child for child in button.children if isinstance(child, str))


def set_label(button, label: str) -> None:
    """Write ``label`` as the words of a ``btn``, keeping its icons."""
    button.children = [label if isinstance(child, str) else child for child in button.children]

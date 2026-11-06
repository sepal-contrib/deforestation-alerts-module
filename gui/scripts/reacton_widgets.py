"""Reacton widget lookup that survives a pre-effect re-render."""


def find_rendered_widget(widget_cls):
    """The first ``widget_cls`` widget in the current render tree, or None.

    Fallback for ``solara.get_widget`` when the element in hand is not the one
    reacton keyed the widget under (see ``gui.solara_app._AppShell``). Walks the same
    render-context tree ``get_widget`` walks; call it only from an effect.
    """
    import reacton.core

    contexts = [reacton.core.get_render_context().context]
    while contexts:
        context = contexts.pop()
        contexts.extend(context.children.values())
        for widget in context.element_to_widget.values():
            if isinstance(widget, widget_cls):
                return widget
    return None

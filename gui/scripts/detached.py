"""Render a Solara tile to a standalone widget for MapApp's widget slots.

WHY THIS EXISTS (the alternative is a duplicate tile stacked over the map):
reacton auto-parents an ``Element`` into the active render context the moment
it is *constructed* (``Element.__init__`` calls ``container_adders[-1].add``).
So the obvious ``reacton.render(SomeTile(...))`` builds ``SomeTile(...)``
while *Page's* render is active, which adds the tile to Page's OWN output tree:
every tile then renders twice, once leaked inline into Page (all tiles stacked
in one tall sheet, bleeding over the fullscreen map) AND once for MapApp's
dialog/panel/step slot. Building the element with Page's ``container_adders``
temporarily detached means the construction does NOT auto-add to Page; only
the render here (in its own isolated context) renders it, and MapApp is handed
that single widget.

The tile renders INTO a container (a plain ``div``), never container-less
(``render_fixed``), for two reasons:

- a step remounts its root on a project switch (keyed on the install signal),
  and a container-less context raises "You are not using a container, and the
  root component returned a new widget" out of the reactive set that triggered
  the render;
- a tile whose render raises is handled by reacton inside the container: its
  slot shows the traceback and nothing is raised into the publisher (an
  auto-save, a worker's ``update_project``), whose other listeners then still
  run. Container-less, reacton's error handling tried to swap the root widget
  and the real exception surfaced as that same unrelated ValueError.

Such a failure is reported too (``_report``): the traceback goes to the app
logger (the server log, see ``notify_bridge.install_server_log_handler``) and
an error toast names the tile. Both run on a helper thread: the observer fires
inside the failed render, under that context's render lock, and publishing a
toast or a task milestone from there would take the shell's render lock in the
opposite order from a shell render that re-renders a tile (a theme toggle, a
set load). reacton keeps the context on the traceback after that; the tile
shows it until the page is reloaded.
"""

import html
import logging

import ipyvuetify as v
import ipywidgets
import reacton

from gui.i18n import t
from gui.scripts.notify_bridge import current_notifier
from gui.scripts.solara_threads import spawn_in_context

logger = logging.getLogger("SEPALUI.deforestation_alerts")


def _error_text(widget) -> str:
    """The traceback reacton rendered in place of the tile."""
    value = widget.value
    if value.startswith("<pre>") and value.endswith("</pre>"):
        value = value[len("<pre>"):-len("</pre>")]
    return html.unescape(value)


def _report(name: str, text: str, notify: bool) -> None:
    def _run():
        logger.error("The %s tile failed to render; it shows the traceback until the page is reloaded:\n%s",
                     name, text.rstrip())
        if notify:
            last = text.rstrip().splitlines()[-1] if text.strip() else ""
            current_notifier().error(t("app.tile_failed", tile=name, error=last))

    spawn_in_context(_run)


def render_detached(make_element, name: str = "tile", notify: bool = True):
    """Render the tile ``make_element`` builds into a standalone ``div``.

    Args:
        make_element: zero-arg callable that constructs the tile Element. Must
            be a thunk (not a pre-built element) so the construction happens
            here, after the adders are detached.
        name: how the tile is named in the log line and the toast when its
            render raises.
        notify: also publish an error toast on such a failure (the shell);
            False keeps the report to the log.

    Returns ``(container, render_context)``: the container widget is what
    MapApp shows in its slot; the context is what a test closes.
    """
    import reacton.core as _rcore

    container = v.Html(tag="div")

    def _on_children(change):
        for widget in change["new"]:
            if isinstance(widget, ipywidgets.HTML):  # reacton's error widget; the tiles are ipyvuetify
                _report(name, _error_text(widget), notify)

    container.observe(_on_children, names="children")
    rc = _rcore.get_render_context(required=False)
    saved = rc.container_adders[:] if rc is not None else None
    if rc is not None:
        del rc.container_adders[:]
    try:
        _, context = reacton.render(make_element(), container=container, handle_error=True)
    finally:
        if rc is not None:
            rc.container_adders[:] = saved
    return container, context

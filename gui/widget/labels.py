"""The analysis screen's translated text, written again on a language switch.

The screen is a raw ipyvuetify tree built once per session (see
``analysis_tile``'s module docstring for why), so nothing re-renders it when
the locale changes: every ``t()`` it ran at build time would keep the
language of that moment. That is always English, because pysepal resolves
the browser's language only when the selector first mounts, after the
shell's first render has built this screen.

Each builder registers a *label hook* instead: a function that writes its
translated text. ``add`` runs it once and keeps it, and ``run`` runs every
hook again. ``build_screen``'s ``relabel`` calls ``run``, and the shell's
``_ScreenBridge`` calls ``relabel`` on every language switch.

A hook writes text only (children strings, labels, hints, placeholders,
error messages, items' text, ``title`` / ``aria-label``, tooltips), never a
``v_model``, ``disabled``, style, class, reactive, file or notification. The
one exception is these audited callbacks; each writes only state derived from
what it already shows, so a run with nothing changed writes the same values:

* ``context._refresh`` — progress bar value, prev/next/unreviewed
  ``disabled``, the id field's placeholder
* ``context._label_facts`` (``_write_facts``) — the facts' swatch style
* ``filter_menu._sync`` — button icon and colour, chip style
* ``filter_menu._label`` (open menu) — attribute items, values ``v_model``
  under ``syncing``
* ``set_selector._sync`` — ``items``, ``v_model`` (the active set) under
  ``syncing``
* ``build_set_status_banner._sync`` — banner style
* ``footer._sync_state_line`` — state line class
* ``earlier._sync_badge`` — badge colour, key style
* ``questions._sync`` — wrappers' style, numbers, marks, controls'
  ``v_model`` (the saved answers; no reactive)
* ``edit_tools.wire_refine_badge._show`` — badge colour
* ``edit_tools._show_found`` — nothing else
* ``export._refresh_gif_gate`` — GIF tile ``disabled``
* ``export._show_badge_for_state`` — badge colour, toolbars show/hide, the
  save-first style
* ``timeline.render`` — redraws the strip (re-picks only on a new alert)

Only those may be registered as they are, in place of their builder's
trailing direct call (``add`` runs them once). Any other callback is audited
the same way and added to this list first.
"""

import logging
from typing import Callable

logger = logging.getLogger("SEPALUI.deforestation_alerts")


class Labels:
    """The label hooks of one analysis screen."""

    def __init__(self):
        self._hooks: list[Callable[[], None]] = []

    def add(self, hook: Callable[[], None], *, now: bool = True) -> Callable[[], None]:
        """Keep ``hook`` for every language switch; run it now unless
        ``now=False`` (a builder that has just written the text itself)."""
        if now:
            hook()
        self._hooks.append(hook)
        return hook

    def run(self) -> None:
        """Run every hook in the order they were added. One that raises is
        logged and the others still run: a label left in the old language is
        better than a screen half relabelled."""
        for hook in list(self._hooks):
            try:
                hook()
            except Exception:
                logger.exception("Analysis: relabelling failed in %s", getattr(hook, "__qualname__", hook))

"""Review-only mode (spec §6.3): a project imported from a review package.

A review copy carries ``Project.review_copy``. Its setup (AOI, sources, masks,
layers, periods, downloads, Integrate, questions, template, grid) is locked:
each protected action refuses with the banner text, and the steps disable
their controls. Analysis Save, the per-alert exports, Overview, the
``questions_expanded`` switch and Return reviews stay allowed.
"""

from deforestation_alerts.errors import KeyedError


def read_only(p) -> bool:
    """True for a review copy (``p.review_copy`` is set)."""
    return p is not None and getattr(p, "review_copy", None) is not None


class ReadOnlyProject(KeyedError, ValueError):
    """A setup change was asked of a review copy. Its message is the banner's."""

    key = "review_copy.read_only"

    @property
    def params(self) -> dict:
        """The message values (``KeyedError.values``), as ``SplitError.params``."""
        return self.values


def refuse_if_read_only(p) -> None:
    """Raise ``ReadOnlyProject`` when ``p`` is a review copy."""
    if read_only(p):
        rc = p.review_copy
        raise ReadOnlyProject(f"project {p.name!r} is a review copy", evaluator=rc.evaluator,
                              origin_project=rc.origin_project)

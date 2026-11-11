"""Review-only mode (spec §6.3): a project imported from a review package.

A review copy carries ``Project.review_copy``. Its setup (AOI, sources, masks,
layers, periods, downloads, Integrate, questions, template, grid) is locked:
each protected action refuses with the banner text, and the steps disable
their controls. Analysis Save, the per-alert exports, Overview, the
``questions_expanded`` switch and Return reviews stay allowed.
"""

from deforestation_alerts.errors import KeyedError


def read_only(p) -> bool:
    """True for a review copy or a converted v1 project."""
    return p is not None and (getattr(p, "review_copy", None) is not None or is_migrated(p))


def is_migrated(p) -> bool:
    return p is not None and getattr(p, "migrated_from", None) is not None


class ReadOnlyProject(KeyedError, ValueError):
    """A setup change was asked of a review copy. Its message is the banner's."""

    key = "review_copy.read_only"

    @property
    def params(self) -> dict:
        """The message values (``KeyedError.values``), as ``SplitError.params``."""
        return self.values


def refuse_if_read_only(p) -> None:
    """Raise ``ReadOnlyProject`` when ``p`` is a review copy or a migrated project."""
    if read_only(p):
        if p.review_copy is None:
            raise ReadOnlyProject(f"project {p.name!r} was migrated from v1", key="migrated.read_only")
        rc = p.review_copy
        raise ReadOnlyProject(f"project {p.name!r} is a review copy", evaluator=rc.evaluator,
                              origin_project=rc.origin_project)

"""Name rules for custom sources and masks (spec §5.1). Solara-free; errors
are message keys the UI translates."""

import itertools
from pathlib import PurePosixPath
from typing import Callable, Optional

from deforestation_alerts.project.models import PREDEFINED_KEYS, Project, sources_slug
from deforestation_alerts.scripts.naming import safe_token

MAX_NAME = 24
_PREDEFINED_TOKENS = {safe_token(k) for k in PREDEFINED_KEYS}  # glad-l, radd, glad-s2


def name_from_ref(ref) -> str:
    """The default name of a raster: an Earth Engine asset id's last segment,
    a file path's stem."""
    text = str(ref or "").strip().rstrip("/")
    if not text:
        return ""
    last = PurePosixPath(text).name
    return PurePosixPath(last).stem if "." in last else last


def suggest_name(base: str, is_ok: Callable[[str], bool], max_len: int = MAX_NAME) -> str:
    """``base`` cut to ``max_len``, else ``<base>_2`` … ``<base>_99`` (the base
    shortened so the suffix fits), the first that ``is_ok`` accepts; "" if none."""
    base = str(base or "").strip()
    if not base:
        return ""
    first = base[:max_len].rstrip()
    if is_ok(first):
        return first
    for n in range(2, 100):
        suffix = f"_{n}"
        candidate = base[: max_len - len(suffix)].rstrip() + suffix
        if is_ok(candidate):
            return candidate
    return ""


def suggest_name_with_tail(base: str, tail: str, is_ok: Callable[[str], bool], max_len: int = MAX_NAME) -> str:
    """``<base><tail>`` where the *base* is shortened so the tail (a combination
    or band suffix such as ``_nir_swir``) stays whole; a clash adds ``_2`` …
    ``_99`` after the tail. A tail too long to fit leaves the whole text to
    ``suggest_name``'s plain cut. "" when nothing is accepted."""
    base, tail = str(base or "").strip(), str(tail or "")
    if not tail or len(tail) >= max_len:
        return suggest_name(base + tail, is_ok, max_len)
    for n in range(1, 100):
        end = tail + (f"_{n}" if n > 1 else "")
        candidate = base[: max_len - len(end)].rstrip() + end
        if is_ok(candidate):
            return candidate
    return ""


def _key(name) -> str:
    return str(name or "").strip().casefold()


def slug_collision(project: Project, new_name: str, current: Optional[str] = None) -> bool:
    """True when two different source combinations (the current sources plus
    ``new_name``) would share a set slug, or one would take the slug of a
    recorded set whose sources differ (spec §5.1)."""
    names = [n for n in project.sources if n != current] + [new_name.strip()]
    seen: dict = {}
    for r in range(1, len(names) + 1):
        for combo in itertools.combinations(names, r):
            slug, members = sources_slug(combo), frozenset(combo)
            if seen.setdefault(slug, members) != members:
                return True
    for s in project.alert_sets.values():
        slug = sources_slug(s.sources)
        if slug in seen and seen[slug] != frozenset(s.sources):
            return True
    return False


def validate_source_name(name, project: Project, *, current: Optional[str] = None) -> Optional[str]:
    text = str(name or "").strip()
    if not text:
        return "steps.alerts.name_empty"
    if len(text) > MAX_NAME:
        return "steps.alerts.name_too_long"
    # alert_codes.format_list joins names with ", " and " and "; parse_formatted_string splits them back
    if ", " in text or " and " in f" {text} ":
        return "steps.alerts.name_separator"
    if _key(text) in {k.casefold() for k in PREDEFINED_KEYS}:
        return "steps.alerts.name_reserved"
    others = [n for n in project.sources if n != current]
    if _key(text) in {_key(n) for n in others}:
        return "steps.alerts.name_duplicate"
    token = safe_token(text)
    if token == "na":
        return "steps.alerts.name_token_na"
    if token in _PREDEFINED_TOKENS or token in {safe_token(n) for n in others}:
        return "steps.alerts.name_token_taken"
    if slug_collision(project, text, current):
        return "steps.alerts.name_set_collision"
    return None


def validate_mask_name(name, project: Project, *, current: Optional[str] = None) -> Optional[str]:
    text = str(name or "").strip()
    if not text:
        return "steps.prepare.mask_name_empty"
    if len(text) > MAX_NAME:
        return "steps.prepare.mask_name_too_long"
    others = [n for n in project.masks if n != current]
    if _key(text) in {_key(n) for n in others}:
        return "steps.prepare.mask_name_duplicate"
    token = safe_token(text)
    if token == "na":
        return "steps.prepare.mask_name_token_na"
    if token in {safe_token(n) for n in others}:
        return "steps.prepare.mask_name_token_taken"
    return None

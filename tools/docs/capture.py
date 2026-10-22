"""Take the user guide's screenshots from the demo project.

    python -m tools.docs.capture --demo DIR --lang {en,es,fr,pt} [--only id,…] [--port N]
        [--data-dir DIR] [--ee-project NAME] [--headed]

``--demo`` is the demo ``tools/docs/demo_project.py`` built: its project folder
(``…/madre_de_dios``) or the data directory holding it. It is never written
to: :func:`copy_demo` copies it (with its ``_fixtures/``) into ``--data-dir``
(default ``~/defo_docs_capture``, wiped first) and the app runs on the copy.

Every ``.. shot: <id> | …`` comment of ``doc/en.rst`` has an entry in
:data:`SHOTS`, run in that order on one app server: each one reaches its state
through the UI (the extra state a shot needs, such as an added question or a
merged reviewed copy, is made in the copy, through the UI as a user would),
checks the UI language, crops what its comment describes and writes
``doc/<lang>/img/<id>.png`` (1440×900 window, light theme, an optimized PNG).
``--only`` runs the listed shots alone; their setups run too, so any subset
works, just slower than in the full order.

Earth Engine runs with the account of whoever runs it; ``--ee-project`` picks
another Cloud project for this run's server only (as for ``demo_project``).
Labels are read from the catalogue of ``--lang``, so the same run works in
every language; :func:`assert_language` stops the run when the app is not in
it.
"""

import argparse
import json
import re
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image

from tools.docs.check_docs import LANGS
from tools.walk import walklib as w

REPO = Path(__file__).resolve().parents[2]
MESSAGES = REPO / "deforestation_alerts" / "message"
PROJECT = "madre_de_dios"
#: The second project of the capture: made in ``project-new``, then used where
#: the demo cannot show a state (its AOI is locked; all three sources are in).
SIDE = "Madre de Dios 2026"
VIEWPORT = {"width": 1440, "height": 900}
#: Marks a data directory this script made, so a later run may wipe it.
MARKER = ".defo-docs-capture"
WIDTH = 1440


@dataclass
class Run:
    lang: str = "en"
    data: Path = Path.home() / "defo_docs_capture"
    out: Path = REPO / "doc" / "en" / "img"
    port: int = 8931
    done: dict = field(default_factory=dict)


#: The current run (``main`` sets it).
RUN = Run()


# ---------------------------------------------------------------- files
def copy_demo(src: Path, data_dir: Path) -> Path:
    """Copy the demo into ``data_dir`` and return the copied project folder.

    ``src`` is the demo project's folder or the data directory holding it.
    ``data_dir`` gets the project, the ``_fixtures/`` (packages, reviewed copy)
    and the evaluator's review copy project parked in ``_fixtures/review_copy/``
    (in the data root, so the app lists it). ``data_dir`` is wiped first when
    an earlier copy made it; any other non-empty folder is refused, and so is
    a folder inside the demo. The demo itself is only read.
    """
    src = Path(src).expanduser().resolve()
    project = src if (src / "project.json").is_file() else src / PROJECT
    if not (project / "project.json").is_file():
        raise FileNotFoundError(f"no demo project in {src}")
    root = project.parent
    data_dir = Path(data_dir).expanduser().resolve()
    if data_dir == root or root in data_dir.parents or data_dir in root.parents or data_dir == project:
        raise ValueError(f"{data_dir} overlaps the demo {root}")
    if data_dir.exists() and any(data_dir.iterdir()):
        if not (data_dir / MARKER).is_file():
            raise ValueError(f"{data_dir} is not empty and was not made by capture.py")
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / MARKER).write_text("made by tools/docs/capture.py; wiped by its next run\n", encoding="utf-8")
    shutil.copytree(project, data_dir / project.name)
    fixtures = root / "_fixtures"
    if fixtures.is_dir():
        shutil.copytree(fixtures, data_dir / "_fixtures", ignore=shutil.ignore_patterns("review_copy"))
        for copy in sorted((fixtures / "review_copy").glob("*/project.json")):
            shutil.copytree(copy.parent, data_dir / copy.parent.name)
    return data_dir / project.name


def optimize_png(path: Path, limit: int = 590 * 1024) -> None:
    """Rewrite ``path`` as a smaller PNG with the same size: zlib at its best
    first, then a 256-colour palette when that is still over ``limit``."""
    path = Path(path)
    with Image.open(path) as img:
        img.load()
        rgb = img.convert("RGB")
    rgb.save(path, optimize=True)
    if path.stat().st_size > limit:
        rgb.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE).save(path, optimize=True)


# ---------------------------------------------------------------- language
def _catalogue_text(code: str, dotted: str):
    for path in sorted((MESSAGES / code).glob("*.json")):
        node = json.loads(path.read_text(encoding="utf-8"))
        for part in dotted.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, str):
            return node
    return None


#: pysepal's own catalogue (the AOI selector's labels) for each guide language.
PYSEPAL_LANGS = {"en": "en", "es": "es", "fr": "fr", "pt": "pt-PT"}


def PT(dotted: str) -> str:
    """The text of pysepal's catalogue key ``dotted`` in the run's language,
    English where pysepal has none (as pysepal shows it)."""
    import pysepal

    root = Path(pysepal.__file__).parent / "message"
    for code in (PYSEPAL_LANGS[RUN.lang], "en"):
        path = root / code / "locale.json"
        if not path.is_file():
            continue
        node = json.loads(path.read_text(encoding="utf-8"))
        for part in dotted.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, str) and node:
            return node
    raise KeyError(f"pysepal {RUN.lang}: {dotted}")


def T(dotted: str, **values) -> str:
    """The text of catalogue key ``dotted`` in the run's language (with its
    ``{placeholders}`` filled from ``values``)."""
    text = _catalogue_text(LANGS[RUN.lang], dotted)
    if text is None:
        raise KeyError(f"{RUN.lang}: {dotted}")
    for k, v in values.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def T_forms(dotted: str) -> list[str]:
    """Every plural form of catalogue key ``dotted`` in the run's language."""
    for path in sorted((MESSAGES / LANGS[RUN.lang]).glob("*.json")):
        node = json.loads(path.read_text(encoding="utf-8"))
        for part in dotted.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, dict) and all(isinstance(v, str) for v in node.values()):
            return list(node.values())
    raise KeyError(f"{RUN.lang}: {dotted} (plural)")


def prepare_labels():
    """The Prepare panel's labels in the run's language, for ``phases.run_all``."""
    from tools.walk import phases as ph

    return ph.prepare_labels(T_forms("steps.prepare.run_all"), T("widgets.product_table.status_ready"),
                             T("widgets.product_table.status_running"))


def set_language(page, lang: str) -> None:
    """Pick ``lang`` in the rail's language selector (pysepal's LocaleSelect)."""
    code = LANGS[lang]
    page.locator(".left-drawer button:has(.mdi-translate)").first.click()
    item = page.locator(f'{w.MODAL} .v-list-item:has-text("({code})")').first
    w.wait_for(lambda: item.count() and item.is_visible(), 15, f"{code} in the language selector")
    item.click()
    w.wait_for(lambda: not page.locator(w.MODAL).count(), 15, "the language selector closed")
    page.wait_for_timeout(1500)


def _leaves(code: str) -> set:
    out = set()

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, str):
            out.add(node.strip())
    for path in sorted((MESSAGES / code).glob("*.json")):
        walk(json.loads(path.read_text(encoding="utf-8")))
    return out


def assert_language(page, lang: str) -> None:
    """Fail unless the app shows ``lang``. Read off the pipeline header: each
    segment's title is its step's label (``"AOI · 1 area"``) or the reason it
    is locked, a text of the app's catalogue. Every one must be a text of
    ``lang``'s catalogue and, out of English, one at least not an English
    text."""
    heads = [t.split(" · ")[0].strip() for t in page.evaluate(
        "() => [...document.querySelectorAll('[data-step-key]')].map(e => e.title || '')")]
    own = _leaves(LANGS[lang])
    english = _leaves("en")
    wrong = [h for h in heads if h not in own]
    if not heads or wrong or (lang != "en" and all(h in english for h in heads)):
        raise AssertionError(f"the app is not in {lang!r}: its pipeline header reads {heads!r}")


# ---------------------------------------------------------------- page helpers
def wait(page, ms):
    page.wait_for_timeout(ms)


def quiet(page, timeout=15.0):
    """No toast, tooltip or hover in the way: move the pointer off the UI,
    wait for the toasts to go (closing any still there) and for spinners to
    stop."""
    page.mouse.move(VIEWPORT["width"] - 3, VIEWPORT["height"] - 3)
    page.evaluate("() => document.activeElement && document.activeElement.blur && document.activeElement.blur()")
    t0 = time.time()
    while time.time() - t0 < timeout and _visible_count(page, ".toast-alert"):
        wait(page, 400)
    for b in page.locator(".toast-alert button:visible").all():
        try:
            b.click(timeout=1000)
        except Exception:  # noqa: BLE001 - gone already
            pass
    t0 = time.time()
    while time.time() - t0 < 5 and _visible_count(page, ".toast-alert"):
        wait(page, 300)
    if _visible_count(page, ".toast-alert"):
        text = " | ".join(t.strip() for t in page.locator(".toast-alert:visible").all_inner_texts())
        raise AssertionError(f"a toast is still on screen after closing it, it would land in the shot: {text}")
    t0 = time.time()
    while time.time() - t0 < timeout and _visible_count(page, ".v-tooltip__content.menuable__content__active"):
        wait(page, 300)
    wait(page, 400)


def _visible_count(page, selector) -> int:
    return page.evaluate("""s => [...document.querySelectorAll(s)].filter(e => {
        const r = e.getBoundingClientRect(); const cs = getComputedStyle(e);
        return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.opacity !== '0'; }).length""",
                         selector)


def boxes(page, selector) -> list:
    """The boxes ``[x, y, w, h]`` of the visible elements matching ``selector``."""
    return page.evaluate("""s => [...document.querySelectorAll(s)].map(e => e.getBoundingClientRect())
        .filter(r => r.width > 0 && r.height > 0).map(r => [r.x, r.y, r.width, r.height])""", selector)


def union(*bs) -> list:
    bs = [b for b in bs if b]
    x0, y0 = min(b[0] for b in bs), min(b[1] for b in bs)
    x1, y1 = max(b[0] + b[2] for b in bs), max(b[1] + b[3] for b in bs)
    return [x0, y0, x1 - x0, y1 - y0]


def box(page, selector, last=False) -> list:
    found = boxes(page, selector)
    if not found:
        raise AssertionError(f"nothing visible matches {selector}")
    return found[-1] if last else found[0]


def content_bottom(page, selector) -> float:
    """The bottom of the visible content inside ``selector`` (its first
    visible match): a panel is as tall as the window, its content is not."""
    return page.evaluate("""s => { const p = [...document.querySelectorAll(s)].find(e => {
            const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
        if (!p) return null;
        const top = p.getBoundingClientRect().top; let bottom = top;
        for (const e of p.querySelectorAll('*')) {
            if (!e.children.length || e.matches('button, .v-input, .v-chip, .v-card, hr')) {
                const r = e.getBoundingClientRect(); const cs = getComputedStyle(e);
                if (r.width > 0 && r.height > 0 && r.height < 500 && cs.visibility !== 'hidden')
                    bottom = Math.max(bottom, r.bottom);
            }
        }
        return bottom; }""", selector)


def panel_box(page, pad_bottom=16) -> list:
    """The Workflow panel, from its top down to its content."""
    b = box(page, ".right-panel")
    bottom = min(content_bottom(page, ".right-panel") + pad_bottom, b[1] + b[3])
    return [b[0], b[1], b[2], bottom - b[1]]


def modal_box(page) -> list:
    return box(page, f"{w.MODAL} .v-card", last=True)


def fit_modal(page):
    """Make the window tall enough for the whole open modal: a long form
    scrolls inside a 900 px window (the width, and so the layout, stays)."""
    need = page.evaluate("""s => { const d = [...document.querySelectorAll(s)].pop();
        return d ? Math.max(d.scrollHeight, (d.firstElementChild || d).scrollHeight) : 0; }""", w.MODAL)
    if need > VIEWPORT["height"] - 60:
        tall(page, int(need) + 120)


def modal_shot(page, shot_id):
    fit_modal(page)
    quiet(page)
    save(page, shot_id, modal_box(page))
    normal(page)


def save(page, shot_id, clip=None, pad=0):
    """Check the language, take the window (or ``clip`` grown by ``pad``,
    kept inside the window) and write the optimized PNG."""
    assert_language(page, RUN.lang)
    path = RUN.out / f"{shot_id}.png"
    RUN.out.mkdir(parents=True, exist_ok=True)
    kw = {}
    if clip:
        vw, vh = page.evaluate("() => [innerWidth, innerHeight]")
        x0, y0 = max(0, clip[0] - pad), max(0, clip[1] - pad)
        x1, y1 = min(vw, clip[0] + clip[2] + pad), min(vh, clip[1] + clip[3] + pad)
        kw["clip"] = {"x": round(x0), "y": round(y0), "width": round(x1 - x0), "height": round(y1 - y0)}
    page.screenshot(path=str(path), **kw)
    optimize_png(path)
    w.log(f"shot {shot_id}: {path.relative_to(REPO) if REPO in path.parents else path} "
          f"({path.stat().st_size // 1024} KB)")


def tall(page, height):
    """A taller window, for a panel whose content runs past 900 px (the
    width stays 1440, so the layout is the same)."""
    page.set_viewport_size({"width": VIEWPORT["width"], "height": height})
    wait(page, 1200)


def normal(page):
    if page.evaluate("() => [innerWidth, innerHeight]") != [VIEWPORT["width"], VIEWPORT["height"]]:
        page.set_viewport_size(VIEWPORT)
        wait(page, 800)


def btn(page, text, scope=""):
    return page.locator(f'{scope} button:visible', has_text=text).first


def exact_btn(page, text, scope=""):
    return page.locator(f'{scope} button:visible',
                        has_text=re.compile(rf"^\s*{re.escape(text)}\s*$", re.IGNORECASE)).first


def modal(page):
    return page.locator(w.MODAL).last


def modal_open(page) -> bool:
    return page.locator(w.MODAL).count() > 0


def field_input(page, label, scope=None):
    scope = scope or w.MODAL
    return page.locator(f'{scope} .v-input:has(label:text-is("{label}")) input:not([type="hidden"])').first


def choose(page, label, option, scope=None, exact=False):
    """Pick ``option`` in the select labelled ``label`` (in ``scope``, the
    open modal by default)."""
    scope = scope or w.MODAL
    page.locator(f'{scope} .v-select:has(label:text-is("{label}")) .v-select__slot').last.click()
    wait(page, 700)
    if exact:
        item = page.locator(".menuable__content__active .v-list-item",
                            has=page.locator(".v-list-item__title",
                                             has_text=re.compile(rf"^\s*{re.escape(option)}\s*$"))).first
    else:
        item = page.locator(".menuable__content__active .v-list-item", has_text=option).first
    item.click()
    wait(page, 700)


def open_select(page, label, scope=None):
    scope = scope or w.MODAL
    page.locator(f'{scope} .v-select:has(label:text-is("{label}")) .v-select__slot').last.click()
    wait(page, 900)


def close_menus(page):
    if page.locator(".menuable__content__active:not(.v-tooltip__content)").count():
        page.keyboard.press("Escape")
        wait(page, 500)


def type_asset(page, asset_id, scope=None):
    """Type ``asset_id`` into pysepal's asset combobox: found by its reload
    icon (``mdi-sync``) or its label in the run's language, not by English text."""
    scope = scope or w.MODAL
    label = PT("widgets.asset_select.label")
    f = page.locator(f'{scope} .v-input:has(.mdi-sync) input:not([type="hidden"]),'
                     f' {scope} .v-input:has(label:text-is("{label}")) input:not([type="hidden"])').last
    f.click()
    f.fill(str(asset_id))
    f.press("Enter")
    f.press("Tab")
    wait(page, 1500)


def switch(page, label, on=True, scope=None):
    scope = scope or w.MODAL
    box_ = page.locator(f'{scope} .v-input:has(label:text-is("{label}"))').last
    if box_.locator("input").first.is_checked() != on:
        box_.locator(".v-input--selection-controls__input").first.click()
        wait(page, 1000)


def cancel_modal(page):
    """Close the open step modal without saving (its Cancel button, else Escape)."""
    if not modal_open(page):
        return
    c = exact_btn(page, T("common.cancel"), w.MODAL)
    if c.count():
        c.click()
    else:
        page.keyboard.press("Escape")
    w.wait_for(lambda: not modal_open(page), 15, "the modal closed")
    wait(page, 600)


def toast_wait(page, t0, text, timeout=120):
    return w.wait_for(lambda: [x for x in w.toasts_since(page, t0) if text in x["msg"]], timeout, f"toast {text!r}")


# ---------------------------------------------------------------- disk state
def manifest(name=PROJECT) -> dict:
    p = RUN.data / name / "project.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def project_names() -> list:
    return sorted(p.parent.name for p in RUN.data.glob("*/project.json"))


# ---------------------------------------------------------------- app states
def reset(page):
    """No menu, step modal or rail dialog open (what a failed shot may leave)."""
    close_menus(page)
    for _ in range(3):
        if not modal_open(page):
            break
        try:
            cancel_modal(page)
        except Exception:  # noqa: BLE001 - a modal without Cancel
            page.keyboard.press("Escape")
            wait(page, 800)
    w.close_dialog(page)


def project_dialog(page):
    reset(page)
    w.back_to_workflow(page)
    w.close_dialog(page)
    page.locator('[data-step-id="2"]').first.click()
    w.wait_for(lambda: w.dialog_visible(page), 30, "the Project dialog")
    wait(page, 1200)


def launch(page):
    """Load the app afresh in the run's language."""
    normal(page)
    w.open_app(page, RUN.port)
    if RUN.lang != "en":
        set_language(page, RUN.lang)
    assert_language(page, RUN.lang)


def ensure_open(page, name=PROJECT):
    """``name`` open. The app is loaded afresh first when another project is
    open: the Evaluation panel of a creator's project, once shown, breaks
    when a review copy is opened in the same session (its creator panel
    renders once more without an alert set and stays on a traceback)."""
    reset(page)
    if not w.title(page).endswith(f"· {name}"):
        if "· " in w.title(page):
            launch(page)
        w.back_to_workflow(page)
        w.close_dialog(page)
        w.open_project(page, name)
        quiet(page)


def step(page, key, wait_ms=2500):
    reset(page)
    w.goto_step(page, key, wait_ms)


# ---------------------------------------------------------------- shots: project
def shot_project_empty(page):
    """Right after the app starts: no project open."""
    project_dialog(page)
    w.wait_for(lambda: T("steps.project.empty_heading") in w.dialog_text(page), 10, "no project open")
    quiet(page)
    save(page, "project-empty", box(page, ".dialog-card"))


def shot_project_new(page):
    """The New project form with a name typed; then created: the capture's
    second project (``SIDE``)."""
    project_dialog(page)
    btn(page, T("steps.project.new"), w.RAIL_DIALOG).click()
    w.wait_for(lambda: modal_open(page), 15, "the New project form")
    field_input(page, T("steps.project.name_label")).fill(SIDE)
    wait(page, 900)
    quiet(page)
    save(page, "project-new", modal_box(page))
    if SIDE in project_names():
        cancel_modal(page)
        return
    page.locator(f"{w.MODAL} {w.ENTER_BUTTON}:visible").last.click()
    w.wait_for(lambda: w.title(page).endswith(f"· {SIDE}"), 60, f"title {SIDE}")
    wait(page, 1500)
    w.close_dialog(page)


def ensure_side(page):
    if SIDE not in project_names():
        project_dialog(page)
        btn(page, T("steps.project.new"), w.RAIL_DIALOG).click()
        w.wait_for(lambda: modal_open(page), 15, "the New project form")
        field_input(page, T("steps.project.name_label")).fill(SIDE)
        wait(page, 900)
        page.locator(f"{w.MODAL} {w.ENTER_BUTTON}:visible").last.click()
        w.wait_for(lambda: w.title(page).endswith(f"· {SIDE}"), 60, f"title {SIDE}")
        wait(page, 1500)
        w.close_dialog(page)
    ensure_open(page, SIDE)


def shot_project_open(page):
    ensure_open(page)
    project_dialog(page)
    quiet(page)
    save(page, "project-open", box(page, ".dialog-card"))
    w.close_dialog(page)


# ---------------------------------------------------------------- shots: AOI
def choose_long(page, select, option, timeout=120):
    """Pick the item reading exactly ``option`` in ``select`` (a ``.v-select``
    locator) whose list is long: Vuetify renders it in steps as it scrolls."""
    w.wait_for(lambda: select.count() and "v-input--is-disabled" not in (select.get_attribute("class") or "")
               and "v-input--is-loading" not in (select.get_attribute("class") or ""), timeout, f"{option}'s select")
    menu = page.locator(".menuable__content__active").last
    item = menu.locator(".v-list-item", has=page.locator(".v-list-item__title", has_text=re.compile(
        rf"^\s*{re.escape(option)}\s*$", re.IGNORECASE))).first
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not menu.count():
            select.locator(".v-select__slot").first.click()
            wait(page, 800)
            continue
        if item.count():
            item.scroll_into_view_if_needed()
            item.click()
            wait(page, 1000)
            return
        menu.evaluate("e => { e.scrollTop = e.scrollTop + e.clientHeight / 2; }")
        wait(page, 300)
    raise AssertionError(f"{option!r} not found")


def aoi_selects(page):
    return page.locator(".right-panel .v-select:visible")


def shot_aoi_methods(page):
    """The AOI method list open, on the capture's second project (no AOI yet)."""
    ensure_side(page)
    step(page, "aoi")
    sel = aoi_selects(page).first
    sel.locator(".v-select__slot").first.click()
    wait(page, 1200)
    quiet(page)
    menu = box(page, ".menuable__content__active:not(.v-tooltip__content)", last=True)
    panel = box(page, ".right-panel")
    save(page, "aoi-methods", [panel[0], panel[1], panel[2], menu[1] + menu[3] + 16 - panel[1]])
    page.keyboard.press("Escape")
    wait(page, 500)


def ensure_side_aoi(page):
    """The capture's second project, open, with its AOI: Admin level 1 →
    Peru → Madre de Dios, Select AOI pressed."""
    ensure_side(page)
    step(page, "aoi")
    if not manifest(SIDE).get("aoi"):
        sel = aoi_selects(page).first
        sel.locator(".v-select__slot").first.click()
        wait(page, 800)
        page.locator(".menuable__content__active .v-list-item", has_text=re.compile(
            rf"^\s*{re.escape(PT('aoi_sel.adm.1'))}\s*$")).first.click()
        wait(page, 1500)
        choose_long(page, aoi_selects(page).nth(1), "Peru")
        choose_long(page, aoi_selects(page).nth(2), "Madre de Dios")
        btn(page, PT("aoi_sel.btn"), ".right-panel").click()
        w.wait_for(lambda: manifest(SIDE).get("aoi"), 600, "the AOI saved")
        wait(page, 4000)


def shot_aoi_admin(page):
    ensure_side_aoi(page)
    quiet(page)
    wait(page, 2500)
    save(page, "aoi-admin", union(box(page, ".right-panel"), [60, 0, 900, 900]))


# ---------------------------------------------------------------- shots: alerts
#: A custom source for the Add dialog: one weekly RADD image of South America, read as RADD.
CUSTOM_SOURCE = "projects/radar-wur/raddalert/v1/sa_20261014"


def panel_shot(page, shot_id, pad_bottom=16):
    quiet(page)
    save(page, shot_id, panel_box(page, pad_bottom))


def shot_alerts_list(page):
    ensure_open(page)
    step(page, "alerts")
    panel_shot(page, "alerts-list")


def open_add_source(page):
    btn(page, T("steps.alerts.add"), ".right-panel").click()
    w.wait_for(lambda: modal_open(page), 15, "the Add source dialog")
    wait(page, 1500)


def shot_alerts_add_predefined(page):
    """On the capture's second project, with GLAD-L alone added, so RADD and
    GLAD-S2 are still offered."""
    ensure_side_aoi(page)
    if "GLAD-L" not in (manifest(SIDE).get("sources") or {}):
        step(page, "alerts")
        open_add_source(page)
        choose(page, T("steps.alerts.predefined_label"), "GLAD-L")
        add = page.locator(f"{w.MODAL} {w.ENTER_BUTTON}:visible").last
        w.wait_for(lambda: add.count() and add.is_enabled(), 180, "GLAD-L's coverage checked")
        add.click()
        w.wait_for(lambda: not modal_open(page), 120, "GLAD-L added")
        w.wait_for(lambda: "GLAD-L" in (manifest(SIDE).get("coverage") or {}), 300, "GLAD-L's coverage saved")
    step(page, "alerts")
    open_add_source(page)
    open_select(page, T("steps.alerts.predefined_label"))
    menu = page.locator(".menuable__content__active .v-list-item")
    w.wait_for(lambda: menu.count() >= 2 and "…" not in page.locator(".menuable__content__active").last.inner_text(),
               180, "the sources' coverage")
    wait(page, 1000)
    quiet(page)
    save(page, "alerts-add-predefined", union(modal_box(page), box(page, ".menuable__content__active", last=True)))
    close_menus(page)
    cancel_modal(page)


def shot_alerts_add_custom(page):
    ensure_open(page)
    step(page, "alerts")
    open_add_source(page)
    choose(page, T("steps.alerts.mode_label"), T("steps.alerts.mode_custom", n=0))
    wait(page, 1200)
    choose(page, T("steps.raster_input.origin_label"), T("steps.raster_input.origin_gee"))
    type_asset(page, CUSTOM_SOURCE)
    w.wait_for(lambda: T("steps.raster_input.bands_loading") not in modal(page).inner_text(), 180, "the bands")
    wait(page, 1500)
    choose(page, T("steps.alerts.format_label"), T("steps.alerts.format_radd"), exact=True)
    wait(page, 1500)
    quiet(page)
    save(page, "alerts-add-custom", modal_box(page))
    cancel_modal(page)


# ---------------------------------------------------------------- shots: layers
#: The demo's layer image (``demo_project.LAYER_IMAGE``): an optical image for the Add dialog.
OPTICAL_IMAGE = "COPERNICUS/S2_SR_HARMONIZED/20260927T144729_20260927T145044_T19LDF"


def map_and_panel(page) -> list:
    """The window right of the rail: the main map and the Workflow panel."""
    rail = box(page, ".left-drawer")
    return [rail[0] + rail[2], 0, VIEWPORT["width"] - rail[0] - rail[2], VIEWPORT["height"]]


def shot_layers_list(page):
    ensure_open(page)
    step(page, "layers")
    on = page.locator(".right-panel button:has(.mdi-map-plus):visible").first
    if on.count():
        on.click()
        w.wait_for(lambda: page.locator(".sepal-legend:visible").count(), 120, "the layer's legend")
    wait(page, 6000)
    quiet(page)
    save(page, "layers-list", map_and_panel(page))


def open_add_layer(page):
    ensure_open(page)
    step(page, "layers")
    btn(page, T("steps.layers.add"), ".right-panel").click()
    w.wait_for(lambda: modal_open(page), 15, "the Add layer dialog")
    wait(page, 1200)


def shot_layers_add_rgb(page):
    open_add_layer(page)
    choose(page, T("steps.layers.field_kind"), T("steps.layers.kind_gee"), exact=True)
    type_asset(page, OPTICAL_IMAGE)
    w.wait_for(lambda: T("steps.raster_input.bands_loading") not in modal(page).inner_text(), 180, "the bands")
    wait(page, 1500)
    switch(page, T("steps.layers.field_rgb"))
    wait(page, 1500)
    choose(page, T("steps.layers.field_combo"), T("steps.layers.combo_false_color"))
    field_input(page, T("steps.layers.field_min")).fill("0")
    field_input(page, T("steps.layers.field_max")).fill("4000")
    switch(page, T("steps.layers.field_show_in_analysis"))
    wait(page, 1200)
    modal_shot(page, "layers-add-rgb")
    cancel_modal(page)


def shot_layers_add_nicfi(page):
    open_add_layer(page)
    choose(page, T("steps.layers.field_kind"), T("steps.layers.kind_nicfi"), exact=True)
    wait(page, 1500)
    field_input(page, T("steps.layers.field_name")).fill(TYPED[RUN.lang]["nicfi_layer"])
    switch(page, T("steps.layers.field_show_in_analysis"))
    wait(page, 1200)
    quiet(page)
    save(page, "layers-add-nicfi", modal_box(page))
    cancel_modal(page)


# ---------------------------------------------------------------- shots: period, prepare
def shot_period_list(page):
    ensure_open(page)
    step(page, "period")
    panel_shot(page, "period-list")


def shot_period_new(page):
    """The New period dialog as it opens: pre-filled for the next period."""
    ensure_open(page)
    step(page, "period")
    btn(page, T("steps.period.new"), ".right-panel").click()
    w.wait_for(lambda: modal_open(page), 15, "the New period dialog")
    wait(page, 1500)
    modal_shot(page, "period-new")
    cancel_modal(page)


def fit_panel(page):
    """Make the window tall enough for the Workflow panel's whole content."""
    over = page.evaluate("""() => Math.max(0, ...[...document.querySelectorAll('.right-panel *')]
        .filter(e => e.clientHeight > 100).map(e => e.scrollHeight - e.clientHeight))""")
    if over > 4:
        tall(page, VIEWPORT["height"] + int(over) + 24)


def shot_prepare_step(page):
    ensure_open(page)
    step(page, "prepare")
    fit_panel(page)
    panel_shot(page, "prepare-step")
    normal(page)


#: The demo's mask (``demo_project.MASK_ASSET``): a forest-cover image for the Add mask dialog.
MASK_ASSET = "UMD/hansen/global_forest_change_2025_v1_13"
MASK_BAND = "treecover2000"


def shot_prepare_mask_add(page):
    ensure_open(page)
    step(page, "prepare")
    btn(page, T("steps.prepare.mask_add"), ".right-panel").click()
    w.wait_for(lambda: modal_open(page), 15, "the Add mask dialog")
    wait(page, 1000)
    choose(page, T("steps.raster_input.origin_label"), T("steps.raster_input.origin_gee"))
    type_asset(page, MASK_ASSET)
    w.wait_for(lambda: T("steps.raster_input.bands_loading") not in modal(page).inner_text(), 180, "the bands")
    wait(page, 1500)
    choose(page, T("steps.prepare.mask_band"), MASK_BAND, exact=True)
    page.locator(f"{w.MODAL} .v-radio", has_text=T("steps.prepare.mask_mode_keep")).first.click()
    wait(page, 1000)
    modal_shot(page, "prepare-mask-add")
    cancel_modal(page)


# ---------------------------------------------------------------- shots: integrate
#: A land-cover image for the attribute layer (ESA WorldCover 2021, band Map).
ATTRIBUTE_IMAGE = "ESA/WorldCover/v200/2021"
ATTRIBUTE_NAME = "worldcover"


def fill_attribute(page):
    """The Add an attribute layer dialog, filled in (not added)."""
    step(page, "integrate")
    btn(page, T("steps.integrate.attr_add"), ".right-panel").click()
    w.wait_for(lambda: modal_open(page), 15, "the Add attribute layer dialog")
    wait(page, 1000)
    choose(page, T("steps.raster_input.origin_label"), T("steps.raster_input.origin_gee"))
    type_asset(page, ATTRIBUTE_IMAGE)
    btn(page, T("steps.integrate.attr_load"), w.MODAL).click()
    loaded = T("steps.integrate.attr_loaded", source=ATTRIBUTE_IMAGE)
    w.wait_for(lambda: loaded in modal(page).inner_text(), 180, "the attribute image's bands")
    wait(page, 1000)
    field_input(page, T("steps.integrate.attr_name")).fill(ATTRIBUTE_NAME)
    open_select(page, T("steps.integrate.attr_bands"))
    page.locator(".menuable__content__active .v-list-item").first.click()
    wait(page, 800)
    close_menus(page)
    wait(page, 1500)


def attribute_listed(page) -> bool:
    columns = T("steps.integrate.attr_columns", columns=f"{ATTRIBUTE_NAME}_map")
    return columns in w.panel_text(page)


def add_attribute(page):
    """Integrate & vectorize's form with one attribute layer: the form keeps
    its layers until the next run, so it is added through the dialog when the
    form has none."""
    ensure_open(page)
    step(page, "integrate")
    if attribute_listed(page):
        return
    fill_attribute(page)
    page.locator(f"{w.MODAL} {w.ENTER_BUTTON}:visible").last.click()
    w.wait_for(lambda: not modal_open(page), 180, "the attribute layer added")
    w.wait_for(lambda: attribute_listed(page), 30, "the attribute layer listed")
    wait(page, 1500)


def shot_integrate_attribute(page):
    ensure_open(page)
    fill_attribute(page)
    modal_shot(page, "integrate-attribute")
    page.locator(f"{w.MODAL} {w.ENTER_BUTTON}:visible").last.click()
    w.wait_for(lambda: not modal_open(page), 180, "the attribute layer added")


def integrate_field(page, key):
    return page.locator(f'.right-panel .v-text-field:has(label:text-is("{T(key)}")) input').first


def shot_integrate_form(page):
    """Other values than the demo's: 0.5 ha, at most 100 alerts, one attribute layer."""
    add_attribute(page)
    integrate_field(page, "steps.integrate.min_size").fill("0.5")
    integrate_field(page, "steps.integrate.max_alerts").fill("100")
    page.keyboard.press("Tab")
    wait(page, 1500)
    fit_panel(page)
    panel_shot(page, "integrate-form")
    normal(page)


def shot_integrate_replace(page):
    """Integrate & vectorize pressed on the reviewed, split demo set: the
    Replace confirmation (cancelled)."""
    ensure_open(page)
    step(page, "integrate")
    btn(page, T("steps.integrate.run"), ".right-panel").click()
    w.wait_for(lambda: modal_open(page), 30, "the Replace confirmation")
    wait(page, 1500)
    modal_shot(page, "integrate-replace")
    cancel_modal(page)


# ---------------------------------------------------------------- analysis helpers
RIGHT_A = ".defo-analysis-right"


def analysis(page):
    """The analysis screen of the open project (Questionnaire's Start analysis)."""
    reset(page)
    if not page.locator(w.SCREEN_ON).count():
        w.goto_step(page, "analysis", 4000)
    w.wait_for(lambda: counter(page), 120, "the queue counter")


def counter(page):
    """``(position, size)`` of the queue counter (``05 / 48``)."""
    found = re.findall(r"(\d+) / (\d+)", page.locator(".defo-topnav-pos").first.inner_text()
                       if page.locator(".defo-topnav-pos").count() else "")
    return (int(found[-1][0]), int(found[-1][1])) if found else None


def go_to(page, number):
    """Show queue number ``number`` (1-based, as the counter shows it). The
    counter's jump menu takes the alert's number in the set, which is the
    queue number while no filter is on; a filtered queue is stepped through
    with Previous / Next."""
    close_menus(page)
    pos, size = counter(page)
    if pos == number:
        return
    if size == len(store(open_project_name(page))):
        page.locator(".defo-jump-btn:visible").first.click()
        menu = page.locator(".menuable__content__active").last
        w.wait_for(lambda: menu.locator("input[type=number]").count(), 15, "the jump menu")
        menu.locator("input[type=number]").first.fill(str(number))
        menu.locator("button", has_text=re.compile(rf"^\s*{re.escape(T('analysis_tile.nav.go'))}\s*$",
                                                   re.IGNORECASE)).first.click()
        w.wait_for(lambda: (counter(page) or (0,))[0] == number, 90, f"alert number {number}")
        wait(page, 3000)
        return
    while counter(page)[0] != number:
        if counter(page)[0] < number:
            next_alert(page)
        else:
            prev_alert(page)


def open_project_name(page) -> str:
    t = w.title(page)
    return t.split("· ", 1)[1] if "· " in t else ""


def prev_alert(page):
    before = counter(page)
    page.locator(f"{w.PANEL} button:has(.mdi-chevron-left):visible", has_text=T("analysis_tile.nav.previous")).first.click()
    w.wait_for(lambda: counter(page) and counter(page)[0] == before[0] - 1, 90, "the previous alert")
    wait(page, 3000)


def next_alert(page):
    before = counter(page)
    page.locator(f"{w.PANEL} button:has(.mdi-chevron-right):visible", has_text=T("analysis_tile.nav.next")).first.click()
    w.wait_for(lambda: counter(page) and counter(page)[0] == before[0] + 1, 90, "the next alert")
    wait(page, 3000)


def open_section(page, title):
    header = page.locator(f"{RIGHT_A} button:visible", has_text=title).first
    w.wait_for(lambda: header.count(), 30, f"the {title} section")
    if not header.locator(".mdi-chevron-down").count():
        header.click()
        wait(page, 900)


def close_section(page, title):
    header = page.locator(f"{RIGHT_A} button:visible", has_text=title).first
    if header.count() and header.locator(".mdi-chevron-down").count():
        header.click()
        wait(page, 900)


def set_verdict(page, verdict):
    page.locator(f"{RIGHT_A} button:visible", has_text=T(f"analysis_tile.verdict.{verdict}")).first.click()
    wait(page, 800)


def answer(page, question, option):
    """Pick ``option`` for ``question`` (its label) in the Questions section."""
    open_section(page, T("analysis_tile.sections.questions"))
    q = page.locator(f"{RIGHT_A} .dat-question", has_text=question).first
    slot = q.locator(".v-select__slot:visible")
    if slot.count():
        slot.last.click()
        wait(page, 700)
        page.locator(".menuable__content__active .v-list-item", has_text=option).first.click()
        page.keyboard.press("Escape")
    else:
        q.locator(":is(button, .v-chip, .v-radio, .v-input--checkbox):visible", has_text=option).first.click()
    wait(page, 700)


def save_review(page):
    """The footer's Save, until its line says the alert is saved."""
    saved = T("analysis_tile.verdict.saved_at", time="").strip()
    footer = page.locator(".defo-analysis-footer").first
    footer.locator("button:visible", has_text=re.compile(
        rf"^\s*{re.escape(T('analysis_tile.export_labels.save_btn'))}\s*$", re.IGNORECASE)).first.click()
    w.wait_for(lambda: saved in footer.inner_text(), 120, "the review saved")
    wait(page, 1500)


def review(page, verdict, driver=None):
    """Answer the alert on screen (verdict, loss driver) and save it."""
    set_verdict(page, verdict)
    if driver:
        answer(page, T("analysis_tile.questionarie.loss_driver_label"), T(f"analysis_tile.questionarie.{driver}"))
    save_review(page)


def store(name=PROJECT):
    import pandas as pd

    m = manifest(name)
    s = m["alert_sets"][m["active_set"]]
    return pd.read_parquet(RUN.data / name / s["store_rel_path"])


def reviewed(name=PROJECT) -> int:
    from deforestation_alerts.project.review_status import reviewed_mask

    return int(reviewed_mask(store(name)).sum())


# ---------------------------------------------------------------- shots: evaluation
#: What the first evaluator does in their review copy before sending it back,
#: by queue number in that copy (their alerts only): a changed verdict on an
#: alert the demo reviewed too (a conflict), then three new reviews.
COPY_PLAN = ((2, "false", None), (3, "true", "loss_driver6"), (4, "true", "loss_driver6"),
             (5, "revision", "loss_driver3"))


def review_copy_name() -> str:
    for name in project_names():
        if manifest(name).get("review_copy"):
            return name
    raise AssertionError(f"no review copy project in {RUN.data}")


def set_dir(name) -> Path:
    m = manifest(name)
    return (RUN.data / name / m["alert_sets"][m["active_set"]]["store_rel_path"]).parent


def returned_zip(name):
    return next(iter(sorted(set_dir(name).glob("*__reviewed.zip"))), None)


def _copy_done(name) -> bool:
    from deforestation_alerts.project.review_status import reviewed_mask

    frame = store(name)
    mask = reviewed_mask(frame)
    want = {4, 6, 8, 10}
    by_id = dict(zip(frame["alert_id"].astype(int), mask))
    status = dict(zip(frame["alert_id"].astype(int), frame["status"]))
    z = returned_zip(name)
    store_path = set_dir(name) / Path(manifest(name)["alert_sets"][manifest(name)["active_set"]]["store_rel_path"]).name
    return (all(by_id.get(i) for i in want) and status.get(4) == "False Positive"
            and z is not None and z.stat().st_mtime >= store_path.stat().st_mtime)


def ensure_return(page):
    """The evaluator's review copy (copied from the demo's fixtures) with
    reviews of its own and a reviewed copy built from them."""
    name = review_copy_name()
    if _copy_done(name):
        return name
    ensure_open(page, name)
    analysis(page)
    for number, verdict, driver in COPY_PLAN:
        go_to(page, number)
        review(page, verdict, driver)
    w.back_to_workflow(page)
    step(page, "evaluators")
    before = returned_zip(name)
    t_before = before.stat().st_mtime if before else 0
    page.locator(".right-panel button.defo-generate-return:visible").first.click()
    w.wait_for(lambda: returned_zip(name) and returned_zip(name).stat().st_mtime > t_before, 300,
               "the reviewed copy rebuilt")
    wait(page, 2000)
    return name


def shot_evaluation_return(page):
    name = ensure_return(page)
    ensure_open(page, name)
    step(page, "evaluators")
    w.wait_for(lambda: page.locator(".right-panel a.defo-download-action:visible").count(), 60,
               "the reviewed copy's download")
    panel_shot(page, "evaluation-return")


def pick_file(page, button_text, path, scope=""):
    """pysepal's FileInputComponent: open its menu and walk from $HOME to ``path``."""
    page.locator(f'{scope} .pysepal-file-input-btn:visible', has_text=button_text).last.click()
    wait(page, 1500)
    for part in Path(path).relative_to(Path.home()).parts:
        item = page.locator(f'.menuable__content__active .v-list-item:has(.v-list-item__title:text-is("{part}"))').first
        w.wait_for(lambda: item.count() and item.is_visible(), 30, f"file menu entry {part}")
        item.click()
        wait(page, 1200)


def merged() -> bool:
    m = manifest()
    return bool(((m["alert_sets"][m["active_set"]].get("split") or {}).get("returns")))


def open_merge_preview(page):
    """Merge reviewed copy… with the evaluator's reviewed copy (put in the
    data folder's ``_fixtures/``, as a creator copies it into their folder)."""
    name = ensure_return(page)
    src = returned_zip(name)
    dest = RUN.data / "_fixtures" / src.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    ensure_open(page)
    step(page, "evaluators")
    btn(page, T("evaluators.merge_open"), ".right-panel").click()
    w.wait_for(lambda: modal_open(page), 15, "the Merge a reviewed copy dialog")
    wait(page, 1000)
    pick_file(page, T("evaluators.import_label"), dest, scope=w.MODAL)
    title = T("evaluators.preview_title", evaluator="Ana")
    w.wait_for(lambda: title in modal(page).inner_text(), 120, "the merge preview")
    wait(page, 1500)


def ensure_merged(page):
    """The demo copy with the evaluator's reviewed copy merged (Keep mine)."""
    if merged():
        return
    open_merge_preview(page)
    t0 = w.now_ms(page)
    page.locator(f"{w.MODAL} {w.ENTER_BUTTON}:visible").last.click()
    w.wait_for(lambda: merged(), 120, "the reviews merged")
    w.wait_for(lambda: not modal_open(page), 30, "the merge dialog closed")
    w.toasts_since(page, t0)
    wait(page, 1500)


def shot_evaluation_merge(page):
    if merged():
        raise AssertionError("the reviewed copy is merged already: run evaluation-merge before evaluation-table")
    open_merge_preview(page)
    modal_shot(page, "evaluation-merge")
    page.locator(f"{w.MODAL} {w.ENTER_BUTTON}:visible").last.click()
    w.wait_for(lambda: merged(), 120, "the reviews merged")
    wait(page, 1500)


def shot_evaluation_table(page):
    ensure_merged(page)
    ensure_open(page)
    step(page, "evaluators")
    panel_shot(page, "evaluation-table")


# ---------------------------------------------------------------- shots: questionnaire
#: What the guide types, per language: the question it adds to the default
#: questionnaire (label, then its options "yes" and "no") and the name of the
#: Planet NICFI layer it fills in (the dialog is cancelled, never saved).
TYPED = {
    "en": {"question": ("Access road nearby?", "Yes", "No"), "nicfi_layer": "NICFI monthly"},
    "es": {"question": ("¿Camino de acceso cerca?", "Sí", "No"), "nicfi_layer": "NICFI mensual"},
    "fr": {"question": ("Route d'accès à proximité ?", "Oui", "Non"), "nicfi_layer": "NICFI mensuel"},
    "pt": {"question": ("Estrada de acesso próxima?", "Sim", "Não"), "nicfi_layer": "NICFI mensal"},
}


def question() -> str:
    return TYPED[RUN.lang]["question"][0]


def question_options() -> tuple[str, str]:
    return TYPED[RUN.lang]["question"][1:]


def question_added() -> bool:
    return any(q.get("label") == question() for q in manifest().get("questions") or [])


def fill_question(page):
    ensure_open(page)
    step(page, "questionnaire")
    btn(page, T("steps.questionnaire.add"), ".right-panel").click()
    w.wait_for(lambda: modal_open(page), 15, "the Add a question dialog")
    wait(page, 1000)
    field_input(page, T("steps.questionnaire.field_label")).fill(question())
    for i, option in enumerate(question_options(), 1):
        btn(page, T("steps.questionnaire.add_option"), w.MODAL).click()
        wait(page, 700)
        field_input(page, T("steps.questionnaire.field_option", n=i)).fill(option)
        wait(page, 400)
    switch(page, T("steps.questionnaire.required"))
    box_ = page.locator(f'{w.MODAL} .v-input--checkbox:has(label:text-is("{T("analysis_tile.verdict.false")}"))').last
    if box_.locator("input").first.is_checked():
        box_.locator(".v-input--selection-controls__input").first.click()
    wait(page, 1200)


def ensure_question(page):
    """The added question (through the dialog), after the default Loss driver."""
    if question_added():
        return
    fill_question(page)
    page.locator(f"{w.MODAL} {w.ENTER_BUTTON}:visible").last.click()
    w.wait_for(lambda: not modal_open(page), 60, "the question added")
    w.wait_for(question_added, 30, "the question saved")
    wait(page, 1500)


def shot_questionnaire_add(page):
    if question_added():
        raise AssertionError("the question is in the project already: run questionnaire-add first")
    fill_question(page)
    modal_shot(page, "questionnaire-add")
    page.locator(f"{w.MODAL} {w.ENTER_BUTTON}:visible").last.click()
    w.wait_for(lambda: not modal_open(page), 60, "the question added")
    w.wait_for(question_added, 30, "the question saved")


def shot_questionnaire_list(page):
    ensure_question(page)
    ensure_open(page)
    step(page, "questionnaire")
    panel_shot(page, "questionnaire-list")


def shot_questionnaire_template(page):
    ensure_question(page)
    ensure_open(page)
    step(page, "questionnaire")
    toggle = page.locator(".right-panel .defo-template-toggle:visible").first
    toggle.click()
    wait(page, 1500)
    fit_panel(page)
    quiet(page)
    card = page.locator(".right-panel .v-card:visible", has=page.locator(".defo-template-toggle")).last
    b = card.bounding_box()
    save(page, "questionnaire-template", [b["x"], b["y"], b["width"], b["height"]], pad=4)
    normal(page)
    toggle.click()
    wait(page, 800)


# ---------------------------------------------------------------- shots: analysis
TOPBAR = ".defo-analysis-topbar"


def filter_panel(page):
    return page.locator(".defo-filter-panel.menuable__content__active").last


def open_filter(page):
    if not filter_panel(page).count():
        page.locator(".defo-filter-btn:visible").first.click()
        w.wait_for(lambda: filter_panel(page).count() and filter_panel(page).is_visible(), 15, "the filter menu")
        wait(page, 1200)


def set_filter(page, attribute, value):
    """The top bar's filter on ``attribute`` = ``value`` (labels as shown)."""
    open_filter(page)
    panel = filter_panel(page)
    choose(page, T("analysis_tile.filter.attribute"), attribute, scope=".defo-filter-panel", exact=True)
    wait(page, 800)
    open_select(page, T("analysis_tile.filter.values"), scope=".defo-filter-panel")
    page.locator(".menuable__content__active:not(.defo-filter-panel) .v-list-item:visible",
                 has_text=re.compile(rf"^\s*{re.escape(value)} \(\d+\)\s*$")).first.click()
    wait(page, 700)
    # Close the values list with a click on the panel's title (Escape would
    # close the panel and drop the pick).
    panel.get_by_text(T("analysis_tile.filter.button"), exact=True).first.click()
    wait(page, 700)
    btn(page, T("analysis_tile.filter.apply"), ".defo-filter-panel").click()
    w.wait_for(lambda: page.locator(".defo-filter-chip:visible").count(), 60, "the filter applied")
    wait(page, 2500)


def clear_filter(page):
    chip = page.locator(".defo-filter-chip:visible")
    if chip.count():
        open_filter(page)
        btn(page, T("analysis_tile.filter.clear"), ".defo-filter-panel").click()
        w.wait_for(lambda: not page.locator(".defo-filter-chip:visible").count(), 30, "the filter cleared")
        wait(page, 2500)


def shot_analysis_topbar(page):
    ensure_merged(page)
    ensure_open(page)
    analysis(page)
    clear_filter(page)
    set_filter(page, T("analysis_tile.filter.col_evaluator"), "Ana")
    go_to(page, 6)
    open_filter(page)
    quiet(page)
    save(page, "analysis-topbar", union(box(page, TOPBAR), box(page, ".defo-filter-panel.menuable__content__active")),
         pad=4)
    page.keyboard.press("Escape")
    close_menus(page)
    clear_filter(page)


#: Reviews made through the Analysis screen of the demo copy, by alert id, on
#: top of the demo's own and the merged ones: 12 reviewed before the guide's
#: alert (id 12, queue number 13).
REVIEW_PLAN = {5: ("true", "loss_driver1"), 7: ("false", None), 9: ("true", "loss_driver1"),
               11: ("true", "loss_driver1")}
GUIDE_ALERT = 12


def maps_ready(page, timeout=180, tries=4):
    """Both maps titled with an image and every tile drawn. A map with the
    "Imagery failed to load" badge (an Earth Engine tile that failed) is
    loaded again, by stepping to a neighbouring alert and back, up to
    ``tries`` times; then the shot fails instead of saving gaps."""
    for attempt in range(tries):
        w.wait_for(lambda: page.evaluate("""() => {
            const maps = [...document.querySelectorAll('.defo-analysis-left .leaflet-container')]
                .filter(m => m.getBoundingClientRect().width > 0);
            return maps.length >= 2 && !document.querySelector('.defo-analysis-left .leaflet-tile-loading')
                && [...document.querySelectorAll('.defo-analysis-left img.leaflet-tile')].every(i => i.complete);
        }"""), timeout, "the maps drawn")
        wait(page, 2500)
        # every view holds its badge, hidden until a tile fails
        failed = page.locator(f'.defo-analysis-left :text-is("{T("analysis.panes.tile_error")}"):visible')
        if not failed.count():
            return
        bad = page.evaluate("""() => [...document.querySelectorAll('.defo-analysis-left img.leaflet-tile')]
            .filter(i => i.complete && !i.naturalWidth).map(i => i.src)""")
        w.log(f"imagery failed to load ({len(bad)} tiles); loading it again ({attempt + 1}/{tries})")
        pos, size = counter(page)
        go_to(page, pos + 1 if pos < size else pos - 1)
        go_to(page, pos)
    raise AssertionError(f"the imagery failed to load {tries} times")


def review_alert(page, verdict, driver=None, road=None):
    """Answer the alert on screen (verdict, loss driver, the added question
    where it is asked) and save it."""
    set_verdict(page, verdict)
    if driver:
        answer(page, T("analysis_tile.questionarie.loss_driver_label"), T(f"analysis_tile.questionarie.{driver}"))
    if verdict != "false" and question_added():
        answer(page, question(), road or question_options()[1])
    save_review(page)


def ensure_reviews(page, plan):
    """The alerts of ``plan`` (alert id → verdict, driver) reviewed, the
    ones not reviewed yet through the Analysis screen."""
    from deforestation_alerts.project.review_status import reviewed_mask

    frame = store()
    done = dict(zip(frame["alert_id"].astype(int), reviewed_mask(frame)))
    todo = [i for i in sorted(plan) if not done.get(i)]
    if not todo:
        return
    ensure_open(page)
    analysis(page)
    clear_filter(page)
    for alert_id in todo:
        go_to(page, alert_id + 1)
        review_alert(page, *plan[alert_id])


def guide_alert(page):
    """The Analysis screen on the guide's alert, the filter off."""
    ensure_merged(page)
    ensure_question(page)
    ensure_reviews(page, REVIEW_PLAN)
    ensure_open(page)
    analysis(page)
    clear_filter(page)
    go_to(page, GUIDE_ALERT + 1)
    maps_ready(page)


def guide_alert_reviewed(page):
    """``guide_alert``, the guide's alert reviewed (True alert, Agriculture
    expansion) as ``analysis-footer`` leaves it."""
    guide_alert(page)
    if store().set_index("alert_id").loc[GUIDE_ALERT, "status"] == "Not reviewed":
        review_alert(page, "true", "loss_driver1")


def shot_analysis_screen(page):
    guide_alert(page)
    if reviewed() != 12 or store().set_index("alert_id").loc[GUIDE_ALERT, "status"] != "Not reviewed":
        raise AssertionError("analysis-screen wants 12 reviewed alerts and the guide's alert not reviewed")
    open_section(page, T("analysis_tile.sections.questions"))
    quiet(page)
    save(page, "analysis-screen")


def shot_analysis_timeline(page):
    guide_alert(page)
    quiet(page)
    save(page, "analysis-timeline", box(page, ".analysis-timeline"), pad=2)


def shot_analysis_console(page):
    guide_alert(page)
    if store().set_index("alert_id").loc[GUIDE_ALERT, "status"] != "Not reviewed":
        raise AssertionError("analysis-console wants the guide's alert not reviewed")
    set_verdict(page, "true")
    answer(page, T("analysis_tile.questionarie.loss_driver_label"), T("analysis_tile.questionarie.loss_driver1"))
    w.wait_for(lambda: T("analysis_tile.verdict.unsaved") in page.locator(RIGHT_A).inner_text(), 30,
               "the unsaved line")
    quiet(page)
    right, foot = box(page, RIGHT_A), footer_box(page)
    save(page, "analysis-console", [right[0], right[1], right[2], foot[1] + foot[3] + 8 - right[1]])


def after_map(page) -> list:
    maps = [b for b in boxes(page, ".defo-analysis-left .leaflet-container") if b[2] > 0]
    return max(maps, key=lambda b: b[0])


def shot_analysis_refine(page):
    guide_alert(page)
    close_section(page, T("analysis_tile.sections.questions"))
    open_section(page, T("analysis_tile.sections.refine"))
    btn(page, T("analysis_tile.edition_labels.start_edit_button"), RIGHT_A).click()
    w.wait_for(lambda: page.locator(".leaflet-draw-toolbar:visible, .leaflet-pm-toolbar:visible").count(), 60,
               "the drawing tools")
    wait(page, 2000)
    row = page.locator(f"{RIGHT_A} :is(div, tr):has(> * :text-is('{T('analysis_tile.model_labels.dl_button1')}'))").last
    t0 = w.now_ms(page)
    row.locator("button:visible", has_text=T("analysis_tile.model_labels.run")).first.click()
    found = T("analysis_tile.model_labels.found", patches="", area="").split("·")[0].strip()
    w.wait_for(lambda: found in page.locator(RIGHT_A).inner_text(), 600, "the model's prediction")
    w.toasts_since(page, t0)
    # *Add to map* is an icon, named by its title
    add = row.locator(f'button[title="{T("analysis_tile.model_labels.dl_button_add")}"]:visible').first
    if add.count() and add.is_enabled():
        add.click()
        wait(page, 3000)
    quiet(page)
    m, right, foot = after_map(page), box(page, RIGHT_A), footer_box(page)
    top = m[1] - 50  # the map's title above it
    bottom = max(m[1] + m[3], foot[1] + foot[3]) + 8
    save(page, "analysis-refine", [m[0], top, right[0] + right[2] - m[0], bottom - top])
    btn(page, T("analysis_tile.edition_labels.stop_edit_button"), RIGHT_A).click()
    wait(page, 1500)
    if modal_open(page):
        btn(page, T("analysis_tile.edition_labels.stop_discard"), w.MODAL).click()
        wait(page, 1500)


def footer_box(page) -> list:
    return box(page, ".defo-analysis-footer")


def shot_analysis_footer(page):
    guide_alert_reviewed(page)
    open_section(page, T("analysis_tile.sections.export"))
    w.wait_for(lambda: T("analysis_tile.sections.export_build") in page.locator(".defo-analysis-footer").inner_text(),
               60, "the export section ready to build")
    quiet(page)
    save(page, "analysis-footer", footer_box(page))


def shot_export_alert_files(page):
    guide_alert_reviewed(page)
    open_section(page, T("analysis_tile.sections.export"))
    footer = page.locator(".defo-analysis-footer").first
    ready = T("analysis_tile.sections.export_files.other", n=3)
    if ready not in footer.inner_text():
        t0 = w.now_ms(page)
        footer.locator(".defo-build-tile").first.click()
        w.wait_for(lambda: ready in footer.inner_text(), 900, "the alert files built")
        w.toasts_since(page, t0)
        wait(page, 2000)
    quiet(page, timeout=60)
    save(page, "export-alert-files", footer_box(page))


# ---------------------------------------------------------------- overview
#: The demo's period. The Overview is taken on the set of a second, later
#: period (made here as a user would: New period with its pre-filled dates,
#: Download & harmonize, Integrate & vectorize), so its trend card has two
#: periods and its Confirmed loss a comparison with the demo's. A period
#: before the demo's would put the demo set out of date (its footprint input).
DEMO_PERIOD = "2026-09-01_2026-09-30"
#: The later period's end, typed into the New period dialog (its start is the
#: pre-filled day after the demo's). The en shots were taken with this end; a
#: pre-filled end would be the day of the run. With ``--raw-cache`` its source
#: downloads (named by source and window) are reused, so every language gets
#: the same October set.
LATER_END = "2026-10-20"
#: Reviews of the later set, by alert id: 16 of its alerts, 11 confirmed.
LATER_PLAN = {0: ("true", "loss_driver6"), 1: ("true", "loss_driver1"), 2: ("true", "loss_driver6"),
              3: ("false", None), 4: ("true", "loss_driver1"), 5: ("revision", "loss_driver3"),
              6: ("true", "loss_driver6"), 7: ("false", None), 8: ("true", "loss_driver1"),
              9: ("true", "loss_driver6"), 10: ("true", "loss_driver1"), 11: ("true", "loss_driver6"),
              12: ("false", None), 13: ("true", "loss_driver1"), 14: ("revision", "loss_driver6"),
              15: ("true", "loss_driver6")}


def later_period():
    return next((p for pid, p in (manifest().get("periods") or {}).items() if pid != DEMO_PERIOD), None)


def later_set():
    p = later_period()
    return next((s for s in manifest()["alert_sets"].values() if p and s.get("period_id") == p["id"]), None)


def select_period(page, start, end):
    step(page, "period")
    row = page.locator('.right-panel div[style*="grid-template-columns"]:visible',
                       has_text=T("steps.period.range", start=start, end=end)).last
    radio = row.locator("button:has(.mdi-radiobox-blank)")
    if radio.count():
        radio.first.click()
    wait(page, 3000)


#: The longest wait for Integrate & vectorize on the later period (seconds).
INTEGRATE_TIMEOUT = 45 * 60


def ensure_later_period(page):
    """The later period with its alert set, that set active."""
    from tools.walk import phases as ph

    if later_set():
        if manifest()["active_set"] != later_set()["id"]:
            raise AssertionError("the later period's set is not the active one")
        return
    ensure_open(page)
    if not later_period():
        step(page, "period")
        btn(page, T("steps.period.new"), ".right-panel").click()
        w.wait_for(lambda: modal_open(page), 15, "the New period dialog")
        wait(page, 1500)
        end = page.locator(f'{w.MODAL} .v-input:has(label:text-is("{T("steps.period.end")}")) input').first
        end.fill(LATER_END)
        wait(page, 800)  # the date reaches the kernel and the form checks again
        t0 = w.now_ms(page)
        page.locator(f"{w.MODAL} {w.ENTER_BUTTON}:visible").last.click()
        ph.wait_toast(page, t0, T("steps.period.created"))
        w.wait_for(lambda: not modal_open(page), 15, "the period dialog closed")
    p = later_period()
    if p["end"] != LATER_END:
        raise AssertionError(f"the later period ends {p['end']}, not {LATER_END}")
    if manifest()["active_period"] != p["id"]:
        select_period(page, p["start"], p["end"])
    step(page, "prepare")
    labels = prepare_labels()
    # The button is always rendered once the project has a grid (the demo's
    # is saved): "Run 0 pending" and disabled when nothing is left, disabled
    # too while a run of this period is going (Stop is enabled then) or when
    # the project is read-only. So wait for it, then run only when it has rows
    # to run, or wait out the run already going, and fail at once otherwise.
    run = ph.run_button(page, labels)
    w.wait_for(lambda: run.count(), 60, "the Run … pending button")
    if ph.pending_count(page, labels) and not run.is_enabled():
        stop = btn(page, T("steps.prepare.stop"), ".right-panel")
        if not (stop.count() and stop.is_enabled()):
            raise AssertionError(f"Run {ph.pending_count(page, labels)} pending is disabled and no prepare job "
                                 f"runs (Stop is disabled): is the project read-only?")
        w.wait_for(lambda: run.is_enabled() or not ph.pending_count(page, labels), 3600, "the running prepare job")
    if ph.pending_count(page, labels):
        ph.run_all(page, {}, out=RUN.data / "_capture", expect_ready=8, timeout=3600,
                   shot_name="later_period_prepared", labels=labels)
    step(page, "integrate")
    limit = f'.right-panel .v-input--checkbox:has(label:text-is("{T("steps.integrate.limit_alerts")}"))'
    if not page.locator(f"{limit} input").first.is_checked():
        page.locator(f"{limit} .v-input--selection-controls__ripple").first.click()
        wait(page, 800)
    integrate_field(page, "steps.integrate.min_size").fill("1")
    integrate_field(page, "steps.integrate.max_alerts").fill("48")
    page.keyboard.press("Tab")
    wait(page, 1500)
    t_int = w.now_ms(page)
    btn(page, T("steps.integrate.run"), ".right-panel").click()
    t0 = time.time()
    while not later_set():
        bad = ph.errors(w.toasts_since(page, t_int))
        if bad:
            raise AssertionError(f"integrate failed: {bad[0]['msg']}")
        if time.time() - t0 > INTEGRATE_TIMEOUT:
            raise TimeoutError(f"no alert set for the later period after {INTEGRATE_TIMEOUT // 60} min")
        wait(page, 5000)
    wait(page, 5000)


# ---------------------------------------------------------------- shots: manage, split
def shot_project_manage(page):
    """Manage projects with the demo, the second project and the review copy."""
    ensure_side(page)
    review_copy_name()
    ensure_open(page)
    w.back_to_workflow(page)
    w.open_manage(page)
    # the open project's row: its title holds the "open" chip (no space
    # before it in the text, so ``walklib.manage_item`` misses it)
    item = page.locator(f"{w.MODAL} .v-list-item").filter(
        has=page.locator(".v-list-item__title .v-chip")).filter(has_text=PROJECT).first
    w.wait_for(lambda: item.count() and item.is_visible(), 30, "the demo listed")
    if "v-list-item--active" not in (item.get_attribute("class") or ""):
        item.locator(".v-list-item__title").first.click()
        wait(page, 800)
    modal_shot(page, "project-manage")
    reset(page)


def active_split():
    m = manifest()
    return m["alert_sets"][m["active_set"]].get("split")


def remove_split(page):
    """Edit split → Remove split, through the dialog."""
    if not active_split():
        return
    step(page, "evaluators")
    page.locator(".right-panel button.defo-split-open:visible").first.click()
    w.wait_for(lambda: modal_open(page), 15, "the Edit the split dialog")
    wait(page, 1000)
    exact_btn(page, T("evaluators.remove"), w.MODAL).click()
    for _ in range(2):  # a confirmation, when there is one, has the same button
        wait(page, 1500)
        again = exact_btn(page, T("evaluators.remove"), w.MODAL)
        if active_split() and modal_open(page) and again.count():
            again.click()
    w.wait_for(lambda: not active_split(), 60, "the split removed")
    w.wait_for(lambda: not modal_open(page), 30, "the dialog closed")
    wait(page, 1500)


def shot_evaluation_split(page):
    """The Split the alerts dialog of the active set, filled in and cancelled.
    In a full run the active set is the later period's, which has no split
    (the Overview setup made it active), so the dialog opens as it is. Run
    alone (``--only``) on the September set, its split is removed first
    through the dialog (in the copy). It is the last shot either way, since
    the evaluation shots need the September split."""
    ensure_open(page)
    remove_split(page)
    step(page, "evaluators")
    page.locator(".right-panel button.defo-split-open:visible").first.click()
    w.wait_for(lambda: modal_open(page), 15, "the Split the alerts dialog")
    wait(page, 1000)
    page.locator(f'{w.MODAL} .v-radio:has(label:text-is("{T("evaluators.mode_names")}"))').first.click()
    wait(page, 800)
    rows = page.locator(f"{w.MODAL} .defo-name-row")
    for i, name in enumerate(("Ana", "Luis")):
        rows.nth(i).locator("input").fill(name)
        wait(page, 400)
    wait(page, 800)
    modal_shot(page, "evaluation-split")
    cancel_modal(page)


def card_box(page, title) -> list:
    """The bordered card whose heading reads ``title`` (in the rail dialog)."""
    b = page.evaluate("""title => {
        const leaf = [...document.querySelectorAll('.dialog-card *')].find(e => e.childElementCount === 0
            && e.textContent.trim() === title && e.getBoundingClientRect().width > 0);
        for (let e = leaf; e; e = e.parentElement) {
            const cs = getComputedStyle(e);
            if (e !== leaf && cs.borderTopWidth !== '0px' && cs.borderTopStyle !== 'none'
                && e.getBoundingClientRect().height > 40) {
                const r = e.getBoundingClientRect(); return [r.x, r.y, r.width, r.height];
            }
        }
        return null; }""", title)
    if not b:
        raise AssertionError(f"no card titled {title!r}")
    return b


def open_overview(page, height=None):
    """The Overview dialog of the demo, measure Area (ha), in a window
    ``height`` tall (the dialog grows with it). The September set gets the
    full run's reviews first, so the comparison with it is the same run alone."""
    september_state(page)
    ensure_later_period(page)
    ensure_reviews(page, LATER_PLAN)
    ensure_open(page)
    w.back_to_workflow(page)
    reset(page)
    if height:
        tall(page, height)
    page.locator('[data-step-id="3"]').first.click()
    w.wait_for(lambda: w.dialog_visible(page) and T("overview_tile.card_numbers") in w.dialog_text(page), 120,
               "the Overview")
    w.wait_for(lambda: T("overview_tile.loading") not in w.dialog_text(page), 120, "the Overview read")
    exact_btn(page, T("overview_tile.measure_area"), ".dialog-card").click()
    wait(page, 4000)
    page.evaluate("() => document.querySelectorAll('.dialog-card .dialog-content').forEach(e => e.scrollTop = 0)")
    quiet(page)


def close_overview(page):
    w.close_dialog(page)
    normal(page)


def shot_overview_dashboard(page):
    open_overview(page, 1500)
    dialog = box(page, ".dialog-card")
    history = card_box(page, T("overview_tile.card_history"))
    save(page, "overview-dashboard", [dialog[0], dialog[1], dialog[2], history[1] + history[3] + 12 - dialog[1]])
    close_overview(page)


def shot_overview_figures(page):
    """The figures, the admin card on its first level below the country."""
    open_overview(page, 2600)
    exact_btn(page, T("overview_tile.admin_level_1"), ".dialog-card").click()
    wait(page, 1500)
    dialog = box(page, ".dialog-card")
    top = card_box(page, T("overview_tile.card_status"))[1]
    bottom = max(card_box(page, T(f"overview_tile.{k}"))[1] + card_box(page, T(f"overview_tile.{k}"))[3]
                 for k in ("card_admin", "card_sources"))
    save(page, "overview-figures", [dialog[0], top - 12, dialog[2], bottom + 12 - (top - 12)])
    close_overview(page)


def shot_export_overview_card(page):
    """The Overview's Export alerts card: Confirmed chosen, its GeoPackage
    written (the tile clicked), the CSV table not."""
    open_overview(page, 2600)
    page.locator(".dialog-card .v-chip:visible",
                 has_text=re.compile(rf"^\s*{re.escape(T('overview_tile.export_scope_confirmed'))}\s*\d")).first.click()
    wait(page, 1500)
    if not list(set_dir(PROJECT).glob("alerts_*confirmed*.gpkg")):
        tile = page.locator(".dialog-card .defo-file-tile:visible", has_text=T("overview_tile.export_gpkg")).first
        tile.click()
        w.wait_for(lambda: list(set_dir(PROJECT).glob("alerts_*confirmed*.gpkg")), 300, "the GeoPackage written")
        wait(page, 4000)
    page.evaluate("() => document.querySelectorAll('.dialog-card .dialog-content')"
                  ".forEach(e => e.scrollTop = e.scrollHeight)")
    wait(page, 800)
    quiet(page)
    save(page, "export-overview-card", card_box(page, T("overview_tile.export_label")), pad=2)
    close_overview(page)


# ---------------------------------------------------------------- shots: use case
#: The use case's reviewed alert in the project: alert id 9 (queue number 10),
#: reviewed in ``REVIEW_PLAN`` (True alert, Agriculture expansion, the added
#: question "No"; its after image shows field-shaped clearings), with a
#: Sentinel-2 after image. (Alert 0, the largest, has no after image under the
#: cloud limit.)
USE_CASE_ALERT = 9
#: The alert of Ana's copy the use case shows, by queue number in her copy:
#: one of her own reviews (COPY_PLAN), with a Sentinel-2 after image.
USE_CASE_COPY_ALERT = COPY_PLAN[1][0]


def main_map(page) -> list:
    """The main map: right of the rail, left of the Workflow panel."""
    rail, panel = box(page, ".left-drawer"), box(page, ".right-panel")
    return [rail[0] + rail[2], 0, panel[0] - rail[0] - rail[2], VIEWPORT["height"]]


def september_state(page):
    """The September set as a full run leaves it for the Overview: Ana's copy
    merged, the question added, ``REVIEW_PLAN`` and the guide's alert reviewed
    (``analysis-footer``). Done before the later period exists, since the
    reviews go into the active set; a no-op once it does."""
    if later_set():
        return
    guide_alert_reviewed(page)


def map_switches(page, on, earlier=False):
    """Integrate & vectorize's map switches: the active set's and, with
    ``earlier``, the earlier sets' (which must be offered then)."""
    step(page, "integrate")
    keys = ["steps.integrate.show_alerts"] + (["steps.integrate.show_earlier"] if earlier else [])
    for key in keys:
        sw = page.locator(f'.right-panel .v-input:has(label:text-is("{T(key)}"))')
        w.wait_for(lambda: sw.count(), 30, f"the switch {T(key)!r}")
        switch(page, T(key), on, scope=".right-panel")


def set_legends(alert_sets, earlier=()) -> list[str]:
    """The legend titles of the active set and of earlier sets."""
    def args(s):
        return {"period": s["period_id"], "sources": " + ".join(s["sources"])}
    return ([T("legend.title.set", **args(s)) for s in alert_sets]
            + [T("legend.name.earlier_set", **args(s)) for s in earlier])


def alert_layers_drawn(page, legends, timeout=180):
    """The alert layers on the main map: each legend listed (the floating
    legend, or its dropdown with several), no layer still being built (the
    switches are held while one is), and no map tile loading."""
    from tools.walk import phases as ph

    def ready():
        text = " | ".join([ph.legend_text(page)] + [o["text"] for o in ph.legend_options(page)])
        return (all(lg in text for lg in legends)
                and not page.locator(".right-panel .v-input--switch.v-input--is-disabled").count()
                and not page.locator(".leaflet-tile-loading").count())
    w.wait_for(ready, timeout, f"the alert layers {legends}")
    wait(page, 4000)  # the vector tiles painted


def shot_use_case_alerts_map(page):
    """The demo's September alerts on the main map over Madre de Dios, from
    Integrate & vectorize's switch; switched off again after. Taken after the
    reviews of the merge and the guide, so the dots carry their colours."""
    september_state(page)
    ensure_open(page)
    map_switches(page, True)
    alert_layers_drawn(page, set_legends([manifest()["alert_sets"][manifest()["active_set"]]]))
    quiet(page)
    save(page, "use-case-alerts-map", main_map(page))
    map_switches(page, False)


def shot_use_case_ana_copy(page):
    """Ana's review copy on the Analysis screen, on one of her reviews."""
    name = ensure_return(page)
    ensure_open(page, name)
    analysis(page)
    go_to(page, USE_CASE_COPY_ALERT)
    open_section(page, T("analysis_tile.sections.questions"))
    maps_ready(page)
    quiet(page)
    save(page, "use-case-ana-copy")


def shot_use_case_reviewed_alert(page):
    """A reviewed alert of the September set in Analysis: its verdict and
    both answers, as ``REVIEW_PLAN`` saved them."""
    september_state(page)
    ensure_open(page)
    analysis(page)
    clear_filter(page)
    if store().set_index("alert_id").loc[USE_CASE_ALERT, "status"] == "Not reviewed":
        raise AssertionError("use-case-reviewed-alert wants its alert reviewed (REVIEW_PLAN)")
    go_to(page, USE_CASE_ALERT + 1)
    # analysis-refine / analysis-footer leave these open in a full run, and a
    # long label there can leave the panel scrolled sideways.
    close_section(page, T("analysis_tile.sections.refine"))
    close_section(page, T("analysis_tile.sections.export"))
    page.evaluate(f"""() => {{
        const r = document.querySelector('{RIGHT_A}');
        for (let e = r; e; e = e.parentElement) e.scrollLeft = 0;
        r.querySelectorAll('*').forEach(e => {{ if (e.scrollLeft) e.scrollLeft = 0; }});
    }}""")
    wait(page, 900)
    open_section(page, T("analysis_tile.sections.questions"))
    maps_ready(page)
    quiet(page)
    save(page, "use-case-reviewed-alert")


def shot_use_case_overview(page):
    """The Overview of the October set: its header and key figures, with the
    comparison with September."""
    open_overview(page, 1500)
    dialog = box(page, ".dialog-card")
    tiles = card_box(page, T("overview_tile.kpi_confirmed"))
    # the next row of cards starts about 10 px below the key figures
    save(page, "use-case-overview", [dialog[0], dialog[1], dialog[2], tiles[1] + tiles[3] + 5 - dialog[1]])
    close_overview(page)


def shot_use_case_periods_map(page):
    """The October set's alerts with September's (earlier sets) on the main map."""
    september_state(page)
    ensure_later_period(page)
    ensure_reviews(page, LATER_PLAN)
    ensure_open(page)
    sets = manifest()["alert_sets"]
    map_switches(page, True, earlier=True)
    alert_layers_drawn(page, set_legends([later_set()], [sets[k] for k in sets if k != later_set()["id"]]))
    quiet(page)
    save(page, "use-case-periods-map", main_map(page))
    map_switches(page, False, earlier=True)


# ---------------------------------------------------------------- the run
#: Every shot of the guide, in the order a full run takes them: each one
#: leaves the copy in the state the next ones build on (a fresh app first;
#: the merge before the questionnaire changes; the split removed last).
SHOTS: dict[str, Callable] = {
    "project-empty": shot_project_empty,
    "project-open": shot_project_open,
    "alerts-list": shot_alerts_list,
    "layers-list": shot_layers_list,
    "layers-add-rgb": shot_layers_add_rgb,
    "layers-add-nicfi": shot_layers_add_nicfi,
    "period-list": shot_period_list,
    "period-new": shot_period_new,
    "prepare-step": shot_prepare_step,
    "prepare-mask-add": shot_prepare_mask_add,
    "integrate-attribute": shot_integrate_attribute,
    "integrate-form": shot_integrate_form,
    "integrate-replace": shot_integrate_replace,
    "project-new": shot_project_new,
    "aoi-methods": shot_aoi_methods,
    "aoi-admin": shot_aoi_admin,
    "alerts-add-predefined": shot_alerts_add_predefined,
    "alerts-add-custom": shot_alerts_add_custom,
    "evaluation-return": shot_evaluation_return,
    "use-case-ana-copy": shot_use_case_ana_copy,
    "evaluation-merge": shot_evaluation_merge,
    "evaluation-table": shot_evaluation_table,
    "questionnaire-add": shot_questionnaire_add,
    "questionnaire-list": shot_questionnaire_list,
    "questionnaire-template": shot_questionnaire_template,
    "analysis-topbar": shot_analysis_topbar,
    "analysis-screen": shot_analysis_screen,
    "analysis-timeline": shot_analysis_timeline,
    "analysis-console": shot_analysis_console,
    "analysis-refine": shot_analysis_refine,
    "analysis-footer": shot_analysis_footer,
    "export-alert-files": shot_export_alert_files,
    "use-case-reviewed-alert": shot_use_case_reviewed_alert,
    "use-case-alerts-map": shot_use_case_alerts_map,
    "overview-dashboard": shot_overview_dashboard,
    "overview-figures": shot_overview_figures,
    "export-overview-card": shot_export_overview_card,
    "use-case-overview": shot_use_case_overview,
    "use-case-periods-map": shot_use_case_periods_map,
    "project-manage": shot_project_manage,
    "evaluation-split": shot_evaluation_split,
}


def ee_tile_route(route):
    """Earth Engine map tiles, fetched for the browser and fetched again on
    an error answer (Earth Engine refuses tiles under load, "Too Many
    Requests"; the browser blocks the error body as an image, so the map
    shows a gap and its "Imagery failed to load" badge). The tile is the
    one the app asked for: only the retry is added."""
    for attempt in range(5):
        try:
            resp = route.fetch(timeout=120000)
        except Exception as e:  # noqa: BLE001 - a network error: try again
            w.log(f"Earth Engine tile: {e.__class__.__name__}; again ({attempt + 1}/5)")
            time.sleep(2 * (attempt + 1))
            continue
        if resp.ok or attempt == 4:
            route.fulfill(response=resp)
            return
        w.log(f"Earth Engine tile {resp.status}; again ({attempt + 1}/5)")
        time.sleep(2 * (attempt + 1))
    route.abort()


def main(argv=None) -> int:
    from playwright.sync_api import sync_playwright

    from tools.docs import demo_project

    global RUN
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", type=Path, required=True, help="the built demo (its project folder or data directory)")
    ap.add_argument("--lang", choices=sorted(LANGS), default="en")
    ap.add_argument("--only", default="", help="comma-separated shot ids (default: all, in order)")
    ap.add_argument("--port", type=int, default=8931)
    ap.add_argument("--data-dir", type=Path, default=Path.home() / "defo_docs_capture",
                    help="where the demo is copied and the app runs (wiped first); keep it under $HOME")
    ap.add_argument("--ee-project", metavar="NAME",
                    help="the Earth Engine (Cloud) project for this run's server (see demo_project)")
    ap.add_argument("--ee-max-requests", type=int, metavar="N", help="as for demo_project")
    ap.add_argument("--raw-cache", type=Path, default=Path.home() / "defo_docs_raw_cache",
                    help="where the app's raw source downloads are kept and reused across runs (the later "
                         "period's, so each language gets the same set; see tools/docs/server_hooks)")
    ap.add_argument("--headed", action="store_true", help="show the browser")
    args = ap.parse_args(argv)
    only = [s.strip() for s in args.only.split(",") if s.strip()]
    unknown = sorted(set(only) - set(SHOTS))
    if unknown:
        ap.error(f"unknown shot ids: {', '.join(unknown)}")
    todo = [s for s in SHOTS if not only or s in only]

    data = args.data_dir.expanduser().resolve()
    copy_demo(args.demo, data)
    log_dir = data / "_capture"
    log_dir.mkdir(exist_ok=True)
    RUN = Run(lang=args.lang, data=data, out=REPO / "doc" / args.lang / "img", port=args.port)
    w.log(f"demo copied to {data}; {len(todo)} shots in {args.lang}")

    demo_project.server_env(args)
    proc = w.start_server(args.port, data, log_dir / "server.log")
    failed = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.headed)
            context = browser.new_context(viewport=VIEWPORT, color_scheme="light", locale=LANGS[args.lang])
            page = context.new_page()
            page.add_init_script(w.TOAST_SPY)
            page.route("https://earthengine.googleapis.com/**/tiles/**", ee_tile_route)
            launch(page)
            for shot_id in todo:
                t0 = time.time()
                try:
                    SHOTS[shot_id](page)
                except Exception as e:  # noqa: BLE001 - report it, go on with the next shot
                    failed[shot_id] = f"{e.__class__.__name__}: {e}"
                    w.log(f"FAILED {shot_id}: {failed[shot_id]}")
                    (log_dir / f"failed-{shot_id}.txt").write_text(traceback.format_exc(), encoding="utf-8")
                    try:
                        page.screenshot(path=str(log_dir / f"failed-{shot_id}.png"))
                        launch(page)
                    except Exception:  # noqa: BLE001
                        traceback.print_exc()
                RUN.done[shot_id] = round(time.time() - t0)
            browser.close()
    finally:
        w.stop_server(proc)
    (log_dir / "report.json").write_text(json.dumps({"seconds": RUN.done, "failed": failed}, indent=1),
                                         encoding="utf-8")
    w.log(f"{len(todo) - len(failed)} of {len(todo)} shots taken" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

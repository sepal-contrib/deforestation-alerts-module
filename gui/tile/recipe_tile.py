"""Recipe tile — raw ipyvuetify widget handed straight to MapApp (Phase 4).

Architecture layer: view — ipyvuetify/pysepal for UI only.

WHY A RAW WIDGET (not a Solara component + ``solara.display``): MapApp renders a
``dialog`` tile's content with ``v-if``, so it DESTROYS the dialog DOM on close
and REBUILDS it on reopen. A widget captured via ``solara.display`` lives inside
an ``Output`` whose view is NOT recreated on that rebuild — so the cards vanish
the 2nd time the dialog is opened (the buttons, being reacton-managed, survived,
which is exactly the symptom that was reported). A plain ipyvuetify widget tree
is re-viewed from its model on reopen like any ipywidget, so it survives. ``Page``
builds this once via ``use_memo`` and hands it to MapApp as the dialog content —
same pattern as ``aux_tile.build_aux_layout``.

The async **load** that the earlier Solara port ran via ``solara.lab.use_task``
is here a daemon thread that toggles the Load button's spinner.

**Feedback goes through the pysepal notification GLOBALS** (``notify`` /
``track_task``), not the ``use_notifications()`` hook: this module builds raw
ipyvuetify widgets and its outcomes are published from daemon worker threads, so
there is no Solara component render in which a hook could be called. The globals
resolve the bus from the current kernel context, which ``spawn_in_context``
propagates onto the worker thread. The inline ``sw.Alert`` surfaces that the load
and save flows used to write to are gone — the toast IS the feedback.

The three handlers are re-architected around the shared **models + services**
(unchanged from the Solara port):

* **New recipe** (if alerts are loaded) resets every domain model + clears
  ``app_state.alerts_gdf`` (tiles re-render from their models), then calls
  ``start_new_recipe``: it sets ``recipe_name``, CREATES the recipe folder in the
  user's SEPAL workspace, and sets ``recipe_folder_path`` (model + ``app_state``)
  so the recipe is persisted from the start — the Save card appears and export
  can write into it.
* **Load recipe** reads the recipe JSON, imports it into the models via their
  ``import_from_dictionary`` services, sets ``recipe_folder_path``, and (if a
  saved alert store exists) loads the alert gdf into ``app_state.alerts_gdf``
  so Overview shows the loaded alerts. The EE/analysis pipeline re-run the old
  load did is DEFERRED (Task 9); navigation sets ``app_state.current_page_view``,
  which the shell's ``_NavBridge`` translates into the ``MapApp`` step change.
* **Save recipe** persists every model to ``<recipe_folder>/recipe_parameters.json``
  via ``save_model_parameters_to_json``. It writes to an EXISTING recipe folder
  only, so the Save card stays hidden until ``app_state.recipe_folder_path`` is
  set (driven reactively via ``.subscribe``). This replaces the old header
  ``RecipeBtn``/``ShareBtn`` custom widget.

**Every path here is REMOTE.** Recipes are user data, so the folder, the recipe
JSON and the alert store all live in the user's SEPAL workspace and are reached
through the ``Workspace`` adapter — never ``open()``/``os.path`` on the
container's filesystem, which is not the user's.
"""

import logging
from pathlib import PurePosixPath

import ipyvuetify as v
from pysepal import sepalwidgets as sw
from pysepal.sepalwidgets.file_input import FileInput
from pysepal.solara.notifications import notify, track_task

from deforestation_alerts.message import cm
from deforestation_alerts.scripts.recipe_helper import (
    create_directory,
    generate_recipe_string,
    load_alert_store,
    save_model_parameters_to_json,
)
from gui.scripts.solara_threads import spawn_in_context
from gui.store.project_writers import writing

logger = logging.getLogger("SEPALUI.deforestation_alerts.recipe")


def normalize_picked_path(workspace, picked):
    """Turn the picker's ``v_model`` into an absolute path inside the workspace.

    WHY: ``FileInput`` builds its entries from ``get_remote_files``, which sets
    ``path=entry.path`` -- the path the SERVER echoes back, not one we built.
    ``Workspace.list`` deliberately does not trust that field, so neither does
    this. Today sepal.io echoes a home-ABSOLUTE path
    (``/home/sepal-user/module_results/deforestation_alerts/<recipe>/...``) and
    this is the identity; the guard exists because the whole recipe store is
    anchored on this value (``PurePosixPath(picked).parent`` becomes
    ``recipe_folder_path``), so a home-RELATIVE or otherwise unrooted echo must
    not silently point the alert store at some other remote folder.

    Accepted, in order:

    * absolute and at/under ``workspace.root`` -> unchanged;
    * home-relative (``module_results/deforestation_alerts/...``) -> re-rooted
      onto the SEPAL home;
    * root-relative (``<recipe>/recipe_parameters.json``) -> joined onto
      ``workspace.root``.

    Anything else (a traversal, or an absolute path outside the results root)
    raises ``FileNotFoundError``: better a visible refusal than a load that
    succeeds against the wrong folder.
    """
    path = PurePosixPath(str(picked))
    root = PurePosixPath(workspace.root)
    if ".." in path.parts:
        raise FileNotFoundError(f"{picked}: path traverses outside the workspace")
    if path.is_absolute():
        if path == root or root in path.parents:
            return str(path)
        raise FileNotFoundError(f"{picked}: not inside {workspace.root}")
    # Relative: either home-relative (the results root's own home-relative
    # prefix leads it) or root-relative.
    rel_root = PurePosixPath(workspace.home_relative(root))
    home = PurePosixPath(*root.parts[: len(root.parts) - len(rel_root.parts)])
    if home.is_absolute() and path.parts[: len(rel_root.parts)] == rel_root.parts:
        return str(home / path)
    return str(root / path)


def start_new_recipe(app_tile_model, app_state, recipe_name, workspace):
    """Create a brand-new recipe in the user's SEPAL workspace, point the models
    at it, and signal navigation to the Aux tile.

    Creates ``<results_root>/<recipe_name>/`` via ``create_directory`` (i.e.
    ``Workspace.ensure_dir``) and writes the resulting REMOTE path to BOTH
    ``app_tile_model.recipe_folder_path`` (serialized into the recipe) and
    ``app_state.recipe_folder_path`` (drives Save-card visibility + the
    Overview/export store location). Mirrors what the load handler does for an
    EXISTING recipe, so a freshly created recipe is persisted from the start
    instead of living only in memory.
    """
    app_tile_model.recipe_name = recipe_name
    folder = create_directory(workspace, recipe_name)
    app_tile_model.recipe_folder_path = folder
    app_state.recipe_folder_path.value = folder
    # Jump to the Aux (Optional files) tile; the shell's _NavBridge translates
    # this current_page_view write into the MapApp step change.
    app_state.current_page_view.value = "aux_tile"


def build_recipe_layout(
    aux_model,
    aoi_date_model,
    alert_filter_model,
    selected_alerts_model,
    analyzed_alerts_model,
    app_tile_model,
    app_state,
    workspace,
    gee_interface=None,
):
    """Build the raw ipyvuetify recipe layout (New recipe + Load recipe).

    Returns the outermost ipyvuetify widget; ``Page`` hands it to MapApp as the
    Recipes dialog content. Built once (no re-render), so the ``.bind()`` chain
    is attached exactly once and entered/loaded values survive dialog reopen.

    Parameters
    ----------
    aux_model, aoi_date_model, alert_filter_model, selected_alerts_model,
    analyzed_alerts_model, app_tile_model : pysepal.model.Model
        Shared recipe domain models. New-recipe resets them; load-recipe imports
        the saved JSON into them.
    app_state : AppState
        Shared ephemeral reactive store (``alerts_gdf`` / ``recipe_folder_path``
        / ``current_page_view``).
    workspace : Workspace
        Session user-file adapter built in ``Page()``. Every recipe file this
        tile reads, writes or lists is a USER file, so it all goes through here
        -- including the picker, which browses the user's SEPAL workspace rather
        than the container's disk.
    gee_interface : GEEInterface, optional
        Accepted for signature parity with the other tiles; the deferred EE
        pipeline re-run (Task 9) will use it.
    """
    # --- New-recipe input: pre-filled generated name, bound once to the model.
    new_recipe_input = sw.TextField(
        class_="pa-1 ma-1 d-flex align-center",
        v_model=generate_recipe_string(),
        hint=cm.recipe_tile.input_hint_set_recipe_name,
        persistent_hint=True,
    )
    app_tile_model.bind(new_recipe_input, "temporary_recipe_name")

    # --- Load-recipe input: choose the recipe JSON; .v_model is the chosen path.
    # pysepal's remote-capable FileInput (NOT the bare ``sw.FileInput``, which
    # browses the CONTAINER's disk): handed the session client it lists the
    # user's SEPAL workspace, so ``.v_model`` is a REMOTE path. ``root`` is
    # passed explicitly — with a client and no root the widget defaults it to ""
    # and then rejects any absolute initial_folder.
    # ASSUMED SERVER SHAPE: the widget's entries carry the path the server
    # echoes (``get_remote_files`` -> ``FileEntry.path``), observed to be
    # home-ABSOLUTE. That is not trusted: ``_run_load`` puts ``v_model`` through
    # ``normalize_picked_path`` before it anchors the store.
    load_recipe_input = FileInput(
        initial_folder=workspace.root,
        root=workspace.root,
        sepal_client=workspace.client,
        class_="pa-1 ma-1 d-flex align-center",
        extensions=[".json"],
    )

    create_btn = sw.Btn(
        msg=cm.recipe_tile.button_new_recipe,
        color="primary",
        class_="ma-5",
    )
    load_btn = sw.Btn(
        msg=cm.recipe_tile.button_load_recipe,
        color="primary",
        class_="ma-5",
    )
    save_btn = sw.Btn(
        msg=cm.recipe_tile.button_save_recipe,
        color="primary",
        class_="ma-5",
    )

    new_card = v.Card(
        class_="pa-3 ma-5",
        hover=True,
        children=[
            v.CardTitle(
                class_="pa-1 ma-1", children=[cm.recipe_tile.card_title_new_recipe]
            ),
            v.CardSubtitle(
                class_="pa-1 ma-1",
                children=[cm.recipe_tile.card_subtitle_new_recipe],
            ),
            new_recipe_input,
            v.CardActions(class_="justify-end", children=[create_btn]),
        ],
    )
    load_card = v.Card(
        class_="pa-3 ma-5",
        hover=True,
        children=[
            v.CardTitle(
                class_="pa-1 ma-1", children=[cm.recipe_tile.card_title_load_recipe]
            ),
            v.CardSubtitle(
                class_="pa-1 ma-1",
                children=[cm.recipe_tile.card_subtitle_load_recipe],
            ),
            load_recipe_input,
            v.CardActions(class_="justify-end", children=[load_btn]),
        ],
    )
    # Save card — written to the EXISTING recipe folder only, so it stays hidden
    # (display:none, not child-swap, so it survives dialog reopen) until a recipe
    # folder exists. Visibility is driven reactively below.
    save_card = v.Card(
        class_="pa-3 ma-5",
        style_="display:none;",
        hover=True,
        children=[
            v.CardTitle(
                class_="pa-1 ma-1", children=[cm.recipe_tile.card_title_save_recipe]
            ),
            v.CardSubtitle(
                class_="pa-1 ma-1",
                children=[cm.recipe_tile.card_subtitle_save_recipe],
            ),
            v.CardActions(class_="justify-end", children=[save_btn]),
        ],
    )

    # Show the Save card only once a recipe folder exists (set by new-recipe
    # create and by load). Subscribed once — the layout is memoized for the
    # session, so the leaked cancel handle is fine.
    def _sync_save_visibility(folder):
        save_card.style_ = "" if folder else "display:none;"

    app_state.recipe_folder_path.subscribe(_sync_save_visibility)
    _sync_save_visibility(app_state.recipe_folder_path.value)

    # ------------------------------------------------------------------
    # New-recipe handler. Thin: if alerts are already loaded, reset every model
    # + clear the shared gdf (tiles re-render from model state); then start the
    # new recipe (create its folder, set the name, navigate to the Aux step).
    # ------------------------------------------------------------------
    def _on_create_click(*_):
        if app_state.alerts_gdf.value is not None:
            aux_model.reset_model()
            aoi_date_model.reset_model()
            alert_filter_model.reset_model()
            selected_alerts_model.reset_model()
            analyzed_alerts_model.reset_model()
            app_tile_model.reset_model()
            app_state.alerts_gdf.value = None

        start_new_recipe(
            app_tile_model, app_state, new_recipe_input.v_model, workspace
        )

    create_btn.on_event("click", _on_create_click)

    # ------------------------------------------------------------------
    # Load. File/IO + model round-trip + gdf load run on a daemon thread so the
    # UI stays responsive; the button shows a spinner, progress is a tracked task
    # in the notification pill and the outcome is a toast.
    # ------------------------------------------------------------------
    def _run_load(task):
        picked = load_recipe_input.v_model
        # The picker's path comes from the SERVER's echoed `path` field; anchor
        # it on the workspace before anything is read or derived from it.
        file_name = normalize_picked_path(workspace, picked) if picked else picked
        if not file_name or not workspace.exists(file_name):
            raise FileNotFoundError(cm.recipe_tile.input_hint_load_recipe_name)

        task.step(cm.recipe_tile.load_step_parameters)
        params = workspace.read_json(file_name)

        # Import the saved parameters into every model (service round-trip).
        aux_model.import_from_dictionary(params)
        aoi_date_model.import_from_dictionary(params)
        selected_alerts_model.import_from_dictionary(params)
        analyzed_alerts_model.import_from_dictionary(params)
        app_tile_model.import_from_dictionary(params)

        folder = str(PurePosixPath(file_name).parent)
        app_tile_model.recipe_folder_path = folder
        # Expose the folder to the Overview download button (the alert store
        # lives here). Kept on app_state so the view layer needn't read the
        # domain model. REMOTE path, like everything else in the recipe.
        app_state.recipe_folder_path.value = folder

        # Load the canonical alert store (parquet preferred, legacy CSV fallback)
        # so Overview renders the alerts. None when the recipe has no store yet.
        task.step(cm.recipe_tile.loader_loading_alert_db)
        loaded = load_alert_store(workspace, folder)
        if loaded is not None:
            app_state.alerts_gdf.value = loaded

        # TODO(Task 9 – analysis tile): the OLD load also re-ran the EE pipeline
        # here (alert re-discovery, filtered alert raster, planet before/after
        # image dictionaries). Deferred until the analysis tile is wired.
        # Jump to the analysis tile; the shell's _NavBridge translates this
        # current_page_view write into the MapApp step change.
        app_state.current_page_view.value = "analysis_tile"

    def _on_load_click(*_):
        load_btn.loading = True
        load_btn.disabled = True

        def _worker():
            # The tracker is driven imperatively rather than with ``with``: the
            # context manager publishes its own raw-exception toast on failure,
            # which would land on top of the friendlier one below.
            task = track_task(cm.recipe_tile.load_task_title, total_steps=2)
            try:
                _run_load(task)
                task.complete()
                notify(cm.recipe_tile.alert_load_recipe_done, type_="success")
            except Exception as e:  # noqa: BLE001 — surfaced as an error toast
                logger.exception("Recipe load failed")
                # fail() before the toast, so the run is already recorded as
                # FAILED (with its reason) in the task log by the time the
                # user reads the message. The pill only shows RUNNING tasks,
                # so it disappears rather than staying on screen.
                task.fail(str(e))
                notify(
                    f"{cm.recipe_tile.input_hint_load_recipe_name}: {e}",
                    type_="error",
                )
            finally:
                load_btn.loading = False
                load_btn.disabled = False

        # spawn_in_context (not a bare Thread): the worker sets solara
        # reactives (alerts_gdf, current_page_view) and publishes notifications,
        # both of which need the kernel context to reach the browser.
        spawn_in_context(_worker)

    load_btn.on_event("click", _on_load_click)

    # ------------------------------------------------------------------
    # Save. Persist every model to <recipe_folder>/recipe_parameters.json via
    # the shared service. Requires an existing recipe folder (the card is hidden
    # until one exists; the guard is a safety net). Runs on a daemon thread for
    # parity with load; the button spins and success/errors are published as
    # toasts.
    # ------------------------------------------------------------------
    def _run_save():
        folder = app_state.recipe_folder_path.value
        if not folder or not workspace.exists(folder):
            raise FileNotFoundError(cm.recipe_tile.input_hint_save_recipe_error)

        file_path = str(PurePosixPath(folder) / "recipe_parameters.json")
        save_model_parameters_to_json(
            workspace,
            file_path,
            aux_model,
            aoi_date_model,
            alert_filter_model,
            selected_alerts_model,
            analyzed_alerts_model,
            app_tile_model,
        )

    def _on_save_click(*_):
        save_btn.loading = True
        save_btn.disabled = True

        def _worker():
            try:
                # Writer tracking: while the recipe files are being written the
                # recipe is registered in project_writers, so any future
                # delete/close path can refuse to act mid-write.
                with writing(app_state.recipe_name.value or "recipe"):
                    _run_save()
                # No task pill for save: it is a single json dump, short enough
                # that the button spinner is the whole progress story.
                notify(cm.recipe_tile.alert_save_recipe_done, type_="success")
            except Exception as e:  # noqa: BLE001 — surfaced as an error toast
                logger.exception("Recipe save failed")
                notify(
                    f"{cm.recipe_tile.input_hint_save_recipe_error}: {e}",
                    type_="error",
                )
            finally:
                save_btn.loading = False
                save_btn.disabled = False

        spawn_in_context(_worker)

    save_btn.on_event("click", _on_save_click)

    # Layout: three cards, each containing its own action button (outcomes are
    # published as notification toasts, not inline). The Save card is hidden
    # until a recipe folder exists (see _sync_save_visibility above).
    return v.Container(
        fluid=True,
        class_="pa-0",
        children=[new_card, load_card, save_card],
    )

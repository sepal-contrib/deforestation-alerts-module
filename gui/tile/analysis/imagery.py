"""AlertImagery sub-component STUB (Task 9.2).

Architecture layer: view. In the OLD ``AnalysisTile`` god-class the imagery
behavior was the bulk of the work: ``image_slider_map_callback`` /
``image_slider_info_callback`` / ``create_horizontal_slide_group_v3`` and the
S2 / Landsat dictionary threads (``start_s2_dictionary_thread`` etc.) driven by
the ``before/after_s2_images_time`` / ``before/after_landsat_images_time``
traitlets observers.

For the FOUNDATION this is a pass-through: the two map panes + the two 3-tab
image-selector cards already live on ``widgets`` (built in ``widgets.py``) and
are rendered by the parent's left panel directly. This component exists to FIX
THE INTER-COMPONENT CONTRACT so Task 9.2 can fill it in without changing the
parent. It currently renders nothing of its own (the panes are in the parent's
left panel); Task 9.2 will move the slide-group orchestration here and react to
the per-alert context the parent stores on ``widgets`` /
``analyzed_alerts_model``.

Props
-----
widgets : AnalysisWidgets
    Shared heavy widgets (maps, imgSelection1/2, scratch slots).
app_state : AppState
    Navigation + alerts source of truth.
selected_alerts_model : SelectedAlertsModel
    Holds ``filtered_alert_raster`` (the original-alert overlay layer source).
analyzed_alerts_model : AlertAnalysisModel
    Per-alert imagery dicts (before/after S2/Landsat/Planet) + their *_time traits.
"""

import solara


@solara.component
def AlertImagery(
    widgets,
    app_state,
    selected_alerts_model,
    analyzed_alerts_model,
):
    """STUB. See module docstring. Renders nothing standalone in the foundation.

    The maps + image selectors are rendered by the parent's left panel directly
    from ``widgets``; Task 9.2 (AlertImagery) implements the slide-group build,
    the image map/info callbacks, and the S2/Landsat dictionary tasks here.
    """
    # TODO(Task 9.2 AlertImagery): port image_slider_map_callback,
    # image_slider_info_callback, create_horizontal_slide_group_v3, and the
    # S2/Landsat dictionary threads (now use_tasks) reacting to the per-alert
    # context the parent stores on widgets/analyzed_alerts_model.
    return

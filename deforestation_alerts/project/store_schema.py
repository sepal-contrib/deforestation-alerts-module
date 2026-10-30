"""The alert store's column names (``alerts.parquet``, one alert per row), in
one place. It imports nothing, so the engine, the scripts and the UI can all
use it."""

#: What ``engine.vectorize.vectorize_alerts`` writes for each alert cluster.
VECTOR_COLUMNS = ("geometry", "alert_type_unique", "count", "alert_date_min", "alert_date_max", "area_ha")

#: The shapes stored beside the active geometry (the alert's polygon): its
#: envelope, its centroid and the polygon a reviewer drew. Parquet keeps
#: only one geometry column, so the store writes these as WKB.
SECONDARY_GEOM_COLUMNS = ("bounding_box", "point", "alert_polygon")
GEOM_COLUMNS = ("geometry", *SECONDARY_GEOM_COLUMNS)

#: What Integrate numbers every alert with, before any attribute layer runs.
ID_COLUMNS = ("alert_id", "set_id")

#: The columns Integrate writes for growth alerts (``project.growth``): the
#: links to the parents it grows from, the automatic part of them, and whether
#: the alert is under the minimum size.
GROWS_FROM = "grows_from"
GROWS_FROM_AUTO = "grows_from_auto"
BELOW_MIN = "below_min"
GROWTH_COLUMNS = (GROWS_FROM, GROWS_FROM_AUTO, BELOW_MIN)

#: The column naming an alert's review group: the main alert's ``alert_id``
#: (``<NA>`` when the alert was not reviewed with grouping on).
REVIEW_GROUP = "review_group"

#: The columns a review Save writes (``gui/scripts/review_save.py``) before
#: and after the answer columns of the questions.
REVIEW_BEFORE = ("status",)
REVIEW_AFTER = ("before_img", "before_img_info", "after_img", "after_img_info",
                "admin1", "admin2", "admin3", "alert_polygon", "area_ha", GROWS_FROM, REVIEW_GROUP)

#: The column an evaluator split writes (``project.split``, spec §3).
EVALUATOR = "evaluator"

#: Every column a store carries before its attribute layers and its questions:
#: ``alert_table.convert_to_geopandas`` on the vectorizer's output, plus the ids.
STORE_COLUMNS = (*VECTOR_COLUMNS, "bounding_box", "point", "status", "alert_sources",
                 "before_img", "before_img_info", "after_img", "after_img_info",
                 "alert_polygon", "description", "admin1", "admin2", "admin3", *ID_COLUMNS)

#: The names no attribute column and no question key may take.
RESERVED_COLUMNS = frozenset({*STORE_COLUMNS, EVALUATOR, *GROWTH_COLUMNS, REVIEW_GROUP})

"""Names, builders and legends of the layers the steps draw on the shared map
(spec §4.3, §9.4): ``src:<source>`` (a preview, then the harmonized raster
under the same key) and ``mask:<mask>``, plus the Overview set's legend and
the earlier sets' names and legends (``alerts:set:<set id>``).

One builder per file layer for the Alerts and Download & harmonize steps and
the job that refreshes them (slice-1 Task 18 minor: they were duplicated).
The layer names are translated now (they are the ipyleaflet layer names); the
legends carry language-neutral ``Label`` keys, translated at render.
"""

from gui.i18n import t
from gui.scripts import legend_specs, raster_map
from gui.scripts.legend_data import Label
from gui.store.workflow_steps import active_period


def preview_name(source: str) -> str:
    return t("legend.name.preview", source=source)


def harmonized_name(source: str, period) -> str:
    return t("legend.name.harmonized", source=source, start=period.start.isoformat(), end=period.end.isoformat())


def mask_name(mask: str) -> str:
    return t("legend.name.mask", name=mask)


def preview_legend(key, source, a, b):
    return legend_specs.alert_legend(key, Label(key="legend.name.preview", args=(("source", source),)),
                                     legend_specs.preview_title(source, a, b))


def harmonized_legend(key, source, period):
    name = Label(key="legend.name.harmonized", args=(("source", source), ("start", period.start.isoformat()),
                                                     ("end", period.end.isoformat())))
    return legend_specs.alert_legend(key, name, legend_specs.harmonized_title(source, period.start, period.end))


def mask_layer_legend(project, mask: str):
    """The mask's legend names the active period's choice (keep, exclude, or
    not used in this period)."""
    period = active_period(project)
    choice = period.masks.get(mask) if period is not None else None
    return legend_specs.mask_legend(f"mask:{mask}", mask, choice)


def set_legend(key, alert_set):
    """The Overview set: its PMTiles and the centroid fallback both draw the
    review-status colours, so one legend covers both."""
    title = Label(key="legend.title.set", args=(("period", alert_set.period_id),
                                                ("sources", " + ".join(alert_set.sources))))
    return legend_specs.status_legend(key, title, title)


def earlier_set_name(alert_set) -> str:
    return t("legend.name.earlier_set", period=alert_set.period_id, sources=" + ".join(alert_set.sources))


def earlier_set_legend(key, alert_set):
    """§9.4's earlier-set row: the four review statuses, lightened."""
    name = Label(key="legend.name.earlier_set", args=(("period", alert_set.period_id),
                                                     ("sources", " + ".join(alert_set.sources))))
    title = Label(key="legend.title.earlier_set", args=(("period", alert_set.period_id),))
    return legend_specs.status_legend(key, name, title, faded=True)


def harmonized_builder(map_, path, key: str, name: str):
    return lambda: raster_map.add_raster_layer(map_, path, key=key, name=name)


def mask_builder(map_, path, key: str, name: str):
    """A mask's aligned file (1 = on, 0 = no-data) in the mask grey."""
    return lambda: raster_map.add_raster_layer(map_, path, key=key, name=name, band=1,
                                               colormap=raster_map.MASK_COLORMAP, vmin=1, vmax=1, nodata=0,
                                               opacity=raster_map.MASK_OPACITY)


def refresh_mask_legends(layers, project) -> None:
    """Give every mask layer on the map the active period's choice (spec
    §7.3), without a rebuild: ``refresh`` with no builder only swaps the
    legend."""
    for key in sorted(layers.on.value):
        if key.startswith("mask:") and key[5:] in project.masks:
            layers.refresh(key, None, layers.content_of(key), legend=mask_layer_legend(project, key[5:]))

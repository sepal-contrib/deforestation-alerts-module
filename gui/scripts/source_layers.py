"""The ``src:<source>`` map layer: one builder for the Alerts and Download &
harmonize steps and the job that refreshes it (slice-1 Task 18 minor: it was
duplicated). Task 29 adds the layer's name and legend here."""

from gui.scripts import raster_map


def harmonized_builder(map_, path, key: str, name: str):
    return lambda: raster_map.add_raster_layer(map_, path, key=key, name=name)

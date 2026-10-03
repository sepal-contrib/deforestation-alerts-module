"""The scene index on disk: ``<project_dir>/imagery/``.

Three files, written together and read together: ``scenes.parquet`` (one row
per granule), ``tiles.parquet`` (each tile's footprint once) and
``index.json`` (what is in them and how old it is).

One index serves the whole project -- every period and every set -- because
the granules over an AOI do not depend on which alerts were run over them. A
reviewer moving between alerts, periods or sets pays no Earth Engine round
trip at all.
"""

import logging
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd

from deforestation_alerts.project import paths
from deforestation_alerts.scripts import local_files
from deforestation_alerts.scripts.scene_index import (
    SCENE_COLUMNS, WGS84, Coverage, PlanetState, SceneManifest, SceneTables, empty_tables,
)

logger = logging.getLogger("SEPALUI.deforestation_alerts.scene_cache")

#: One lock per project directory. Both the background widener and a
#: reviewer's "one more month" write ``scenes.parquet``; the lock is what
#: makes the loser merge onto the winner's rows instead of over them.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(pdir) -> threading.Lock:
    key = str(Path(pdir).resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def read_index(pdir) -> tuple[Optional[SceneTables], Optional[SceneManifest]]:
    """The cached index, or ``(None, None)`` when there is none to read.

    A half-written or unreadable index reads as absent rather than raising:
    the caller's answer to both is the same, a rebuild.
    """
    manifest_path = paths.scene_manifest_path(pdir)
    if not (manifest_path.exists() and paths.scenes_path(pdir).exists()):
        return (None, None)
    try:
        manifest = SceneManifest.model_validate_json(manifest_path.read_text())
        scenes = pd.read_parquet(paths.scenes_path(pdir))
        tiles = gpd.read_parquet(paths.tiles_path(pdir))
    except Exception as exc:  # noqa: BLE001 -- any unreadable file is a cache miss
        logger.warning("unreadable scene index in %s: %s", pdir, exc)
        return (None, None)
    return (SceneTables(scenes=scenes[list(SCENE_COLUMNS)], tiles=tiles), manifest)


def _write_manifest(pdir, manifest: SceneManifest) -> None:
    with local_files.write_into(paths.scene_manifest_path(pdir)) as part:
        part.write_text(manifest.model_dump_json(indent=2))


def write_index(pdir, tables: SceneTables, manifest: SceneManifest) -> None:
    """All three files, each replaced atomically -- and in an order that makes
    every intermediate state a consistent one.

    ``write_into`` makes each individual replacement atomic, but three
    replacements are not one transaction: a crash, or a reader arriving
    between two of them, sees a mix. The order is what makes every mix
    readable, and it runs **tiles, scenes, manifest**:

    * tiles first, because a tile footprint nothing references yet is inert,
      whereas a scene row naming a tile the tiles table does not hold would
      be silently invisible to ``overlapping_tiles`` -- a scene that exists
      and cannot be found;
    * the manifest last, because it is the thing that claims coverage, and a
      manifest describing less than the parquets hold only costs a refetch.

    So the three states a crash can leave are new/old/old, new/new/old and
    new/new/new, and all three answer correctly. ``read_index`` therefore
    needs no lock.
    """
    with local_files.write_into(paths.tiles_path(pdir)) as part:
        gpd.GeoDataFrame(tables.tiles, geometry="geometry", crs=WGS84).to_parquet(part, index=False)
    with local_files.write_into(paths.scenes_path(pdir)) as part:
        tables.scenes.to_parquet(part, index=False)
    _write_manifest(pdir, manifest)


def merge_tables(old: SceneTables, new: SceneTables) -> SceneTables:
    """``new`` onto ``old``, the new row winning where both have one.

    The identity of a granule is ``(source, key)``: a refetched tail brings
    back scenes the index already holds, and a corrected cloud figure should
    replace the old one rather than add a second row that would skew the
    per-alert mean.
    """
    scenes = pd.concat([old.scenes, new.scenes], ignore_index=True)
    scenes = (
        scenes.drop_duplicates(subset=["source", "key"], keep="last")
        .sort_values(["date", "source", "tile"], kind="stable")
        .reset_index(drop=True)
    )
    tiles = pd.concat([old.tiles, new.tiles], ignore_index=True)
    tiles = tiles.drop_duplicates(subset=["tile"], keep="last").reset_index(drop=True)
    return SceneTables(
        scenes=scenes[list(SCENE_COLUMNS)],
        tiles=gpd.GeoDataFrame(tiles, geometry="geometry", crs=WGS84),
    )


def store_chunk(
    pdir, new_tables: SceneTables, window: tuple[date, date], digest: str, *, now: datetime,
    planet: Optional[PlanetState] = None, replace: bool = False,
) -> tuple[SceneTables, SceneManifest]:
    """Merge one fetched window into the index on disk and return the result.

    The read, merge and write all happen under the project's lock, and the
    read happens *inside* it rather than being passed in, so a concurrent
    writer's rows are never lost. ``replace`` discards whatever was there --
    a rebuild. An index built for another AOI is discarded the same way
    whether or not ``replace`` was asked for: its tile footprints describe a
    different area.

    ``planet`` is the NICFI access state this fetch observed. ``None`` keeps
    whatever the manifest already recorded, so a widening chunk that did not
    ask Planet anything does not erase the answer the first build got.
    """
    with _lock_for(pdir):
        old, old_manifest = read_index(pdir)
        stale_aoi = old_manifest is not None and old_manifest.aoi_digest != digest
        if replace or old is None or stale_aoi:
            tables = merge_tables(empty_tables(), new_tables)
            covered = Coverage(start=window[0], end=window[1])
        else:
            tables = merge_tables(old, new_tables)
            covered = Coverage(
                start=min(old_manifest.covered.start, window[0]),
                end=max(old_manifest.covered.end, window[1]),
            )
        counts = {str(k): int(v) for k, v in tables.scenes["source"].value_counts().items()}
        kept = old_manifest.planet if (old_manifest is not None and not stale_aoi) else None
        manifest = SceneManifest(
            aoi_digest=digest, refreshed=now, covered=covered, counts=counts,
            planet=planet or kept or PlanetState(),
        )
        write_index(pdir, tables, manifest)
        logger.info("scene index of %s: %d rows, %s..%s", pdir, len(tables.scenes), covered.start, covered.end)
        return (tables, manifest)

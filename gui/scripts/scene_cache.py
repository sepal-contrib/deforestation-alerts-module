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
    INDEX_VERSION, SCENE_COLUMNS, WGS84, ChunkRecord, Coverage, PlanetState, SceneManifest, SceneTables,
    empty_tables,
)

logger = logging.getLogger("SEPALUI.deforestation_alerts.scene_cache")

#: One lock per project directory. Both the background widener and a
#: reviewer's "one more month" write ``scenes.parquet``; the lock is what
#: makes the loser merge onto the winner's rows instead of over them.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(pdir) -> threading.Lock:
    """The lock that serializes ``store_chunk`` calls for one project directory.

    This is a same-process lock: it serializes threads (the background
    widener, a reviewer's "one more month"), the only kind of concurrent
    caller this app has today. It does **not** serialize two separate OS
    processes writing the same project directory -- ``os.replace`` keeps
    each individual file from corrupting or half-writing either way, but
    without this lock in a shared process, two processes would each merge
    onto a pre-write snapshot, the later ``write_index`` would silently
    discard the earlier one's rows, *and* its ``min``/``max`` coverage union
    would still claim the months whose rows it just dropped -- a permanent,
    silent hole that does not self-heal, since the 24h ``"tail"`` refresh in
    ``scene_query.needs_refresh`` only ever revisits the last ``TAIL_DAYS``.
    If cross-process safety is ever needed, the stdlib escape hatch is an
    ``fcntl.flock`` on a sidecar lock file in ``imagery_dir(pdir)``.
    """
    key = str(Path(pdir).resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def read_index(pdir) -> tuple[Optional[SceneTables], Optional[SceneManifest]]:
    """The cached index, or ``(None, None)`` when there is none to read.

    A half-written, unreadable or wrong-layout index reads as absent rather
    than raising: the caller's answer to all three is the same, a rebuild.
    This is why the column projection and dtype fix-up below live *inside*
    the ``try`` -- a ``scenes.parquet`` that parses fine but was written by a
    different layout (missing or renamed columns) must read as a cache miss
    too, not raise a bare ``KeyError`` out of here and out of ``store_chunk``.
    """
    manifest_path = paths.scene_manifest_path(pdir)
    if not (manifest_path.exists() and paths.scenes_path(pdir).exists()):
        return (None, None)
    try:
        manifest = SceneManifest.model_validate_json(manifest_path.read_text())
        if manifest.version != INDEX_VERSION:
            # The layout gate: a manifest written by another release may well
            # parse (pydantic ignores fields it does not know), but its
            # parquet columns are not a promise this build made. Treating it
            # as absent sends the caller straight to a rebuild instead of a
            # merge over an assumption that may not hold.
            logger.info("scene index of %s is version %d, this build reads %d: rebuilding",
                        Path(pdir).name, manifest.version, INDEX_VERSION)
            return (None, None)
        scenes = pd.read_parquet(paths.scenes_path(pdir))
        tiles = gpd.read_parquet(paths.tiles_path(pdir))
        # ``pd.read_parquet`` hands back pandas' ``StringDtype`` for these
        # columns; ``scene_index._scene_frame`` ends every freshly-parsed
        # chunk with the same cast, so a re-read frame must match it too --
        # otherwise a freshly-fetched chunk and a re-read one are not
        # interchangeable ``SceneTables``.
        scenes = scenes[list(SCENE_COLUMNS)].astype({"source": object, "tile": object, "key": object})
    except Exception as exc:  # noqa: BLE001 -- any unreadable or wrong-shaped file is a cache miss
        logger.warning("unreadable scene index in %s: %s", pdir, exc, exc_info=True)
        return (None, None)
    return (SceneTables(scenes=scenes, tiles=tiles), manifest)


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


def _with_chunk(existing, record: ChunkRecord) -> list[ChunkRecord]:
    """``existing`` with ``record`` added, one entry per window.

    A window fetched twice -- the 24h tail, or a retry of a chunk that failed
    -- replaces its earlier entry rather than adding a second, so the list
    stays a description of the index rather than a log of every request ever
    made against it.
    """
    kept = [c for c in existing if (c.start, c.end) != (record.start, record.end)]
    return kept + [record]


def note_failed_chunk(pdir, window: tuple[date, date], digest: str, *,
                      now: datetime) -> Optional[SceneManifest]:
    """Record that ``window`` was requested and did not land.

    Spec §9: ``chunks`` records per-chunk success, so a partial failure still
    leaves a usable index *and* a reader can tell which months are missing
    from it instead of reading an empty month as "no images there". Only the
    manifest is rewritten -- nothing was fetched to put in the parquet files,
    and the coverage deliberately does not grow over a window that failed.

    ``None`` when there is no index for this AOI to record it on, which is the
    case when the very first chunk is the one that failed: there is no partial
    index then, and the caller's error message is the whole story.
    """
    with _lock_for(pdir):
        _, manifest = read_index(pdir)
        if manifest is None or manifest.aoi_digest != digest:
            return None
        record = ChunkRecord(start=window[0], end=window[1], fetched_at=now, ok=False)
        manifest = manifest.model_copy(update={"chunks": _with_chunk(manifest.chunks, record)})
        _write_manifest(pdir, manifest)
        logger.info("scene index of %s: chunk %s..%s did not land",
                    Path(pdir).name, window[0], window[1])
        return manifest


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
        # ``read_index`` already turns a wrong-version manifest into a cache
        # miss (``old_manifest is None``), but checking the version again
        # here makes ``store_chunk`` safe on its own rather than safe only
        # because its one caller today happens to go through ``read_index``.
        stale = old_manifest is not None and (
            old_manifest.aoi_digest != digest or old_manifest.version != INDEX_VERSION
        )
        record = ChunkRecord(start=window[0], end=window[1], fetched_at=now)
        if replace or old is None or stale:
            tables = merge_tables(empty_tables(), new_tables)
            covered = Coverage(start=window[0], end=window[1])
            # This chunk *is* the index now, so it is the whole chunk list
            # too: keeping the discarded index's entries would describe
            # months that are no longer in ``scenes.parquet``.
            chunks = [record]
        else:
            tables = merge_tables(old, new_tables)
            # INVARIANT, not enforced by a runtime check here: ``Coverage`` is
            # contiguous by its own contract (no gap between ``start`` and
            # ``end``). This ``min``/``max`` union only keeps that promise
            # because its one caller, ``scene_query.refresh_window``, always
            # anchors a widen at ``covered.start`` or ``covered.end``, so
            # ``window`` always overlaps or abuts what is already covered. A
            # window that left a gap would still pass this union and claim
            # coverage for dates nobody fetched -- a silent hole, exactly like
            # storing (Jul 1, Jul 31) onto a [May 1, May 31] index, which
            # would claim June without ever having fetched it. (A literal
            # assertion of this was tried and reverted -- see "Fix round 1,
            # item 7" in the task report: the existing concurrency test above
            # stores windows truncated at day 28, which leaves a real 2-day
            # gap against the existing coverage purely for test-data brevity,
            # and would trip a strict runtime check.)
            covered = Coverage(
                start=min(old_manifest.covered.start, window[0]),
                end=max(old_manifest.covered.end, window[1]),
            )
            chunks = _with_chunk(old_manifest.chunks, record)
        counts = {str(k): int(v) for k, v in tables.scenes["source"].value_counts().items()}
        kept = old_manifest.planet if (old_manifest is not None and not stale) else None
        manifest = SceneManifest(
            aoi_digest=digest, refreshed=now, covered=covered, counts=counts,
            planet=planet or kept or PlanetState(), chunks=chunks,
        )
        write_index(pdir, tables, manifest)
        logger.info("scene index of %s: %d rows, %s..%s",
                    Path(pdir).name, len(tables.scenes), covered.start, covered.end)
        return (tables, manifest)

"""Earth Engine settings for the demo build's app server only.

``tools.docs.demo_project`` puts this folder on the server's ``PYTHONPATH``
when it is given ``--ee-project``, ``--ee-max-requests`` or ``--raw-cache``
(``tools.docs.capture``); Python imports this
module at start-up in every process of that server (the Voila kernel
included). Nothing here touches the user's Earth Engine configuration, and
without the variables below nothing happens.

- ``DEFO_DOCS_EE_PROJECT``: every ``ee.Initialize`` call uses this Cloud
  project instead of the one in ``~/.config/earthengine/credentials``
  (pysepal's ``init_ee`` passes that file's project explicitly). The builder
  also hands the server an ``EARTHENGINE_TOKEN`` naming the same project, which
  eeclient's sessions read before that file.
- ``DEFO_DOCS_EE_MAX_REQUESTS``: caps geedim's concurrent tile requests (its
  default is 32) and retries a tile on Earth Engine's "Too Many Requests"
  errors, for a project in *Restricted Mode* (the non-commercial tier's
  reduced quota).
- ``DEFO_DOCS_RAW_CACHE``: a folder of raw source downloads. Before the app
  downloads a source (``engine.download.export_native``) into a file named
  ``raw_<source>_<start>_<end>.tif``, a file of that name in the folder is
  copied in its place, and a new download is copied into the folder. The name
  holds the source and the search window, so a later capture of the same
  period (in any language) gets the same source data; harmonizing, integrating
  and the project's records run as usual.

A build tool only: the app never loads it.
"""

import os

_PROJECT = os.environ.get("DEFO_DOCS_EE_PROJECT")
_N = os.environ.get("DEFO_DOCS_EE_MAX_REQUESTS")
_RAW_CACHE = os.environ.get("DEFO_DOCS_RAW_CACHE")

if _PROJECT:
    try:
        import ee

        _initialize = ee.Initialize

        def _initialize_with_project(*args, **kwargs):
            kwargs["project"] = _PROJECT
            if len(args) > 3:  # Initialize(credentials, url, cloud_api_key, http_transport, project)
                args = args[:4]
            return _initialize(*args, **kwargs)

        ee.Initialize = _initialize_with_project
    except Exception as exc:  # noqa: BLE001 - never break the interpreter's start-up
        print(f"server_hooks: Earth Engine project not set: {exc!r}")

if _N:
    try:
        import ee
        from geedim import tile as _tile
        from googleapiclient.errors import HttpError

        _cap = max(1, int(_N))
        _init = _tile.Tiler.__init__

        def _throttled_init(self, *args, **kwargs):
            kwargs["max_requests"] = min(_cap, int(kwargs.get("max_requests") or _cap))
            _init(self, *args, **kwargs)

        _tile.Tiler.__init__ = _throttled_init
        _tile.Tiler._retry_exceptions = (*_tile.Tiler._retry_exceptions, HttpError, ee.EEException)
        _tile.Tiler._map_tile.__defaults__ = (8, 2.0)  # max_retries, backoff_factor: up to ~8 min per tile
    except Exception as exc:  # noqa: BLE001 - never break the interpreter's start-up
        print(f"server_hooks: Earth Engine throttle not applied: {exc!r}")

if _RAW_CACHE:
    try:
        import shutil
        from pathlib import Path

        from deforestation_alerts.engine import download as _download

        _cache = Path(_RAW_CACHE)
        _export_native = _download.export_native

        def _cached_export_native(image, key_or_native, aoi, out_path, **kwargs):
            out_path = Path(out_path)
            hit = _cache / out_path.name
            if out_path.name.startswith("raw_") and hit.is_file():
                print(f"server_hooks: raw download from the cache: {hit}")
                out_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(hit, out_path.with_name(out_path.name + ".part"))
                out_path.with_name(out_path.name + ".part").replace(out_path)
                return out_path
            result = _export_native(image, key_or_native, aoi, out_path, **kwargs)
            if out_path.name.startswith("raw_") and Path(result).is_file():
                _cache.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(result, _cache / (out_path.name + ".part"))
                (_cache / (out_path.name + ".part")).replace(hit)
            return result

        _download.export_native = _cached_export_native
    except Exception as exc:  # noqa: BLE001 - never break the interpreter's start-up
        print(f"server_hooks: raw download cache not set: {exc!r}")

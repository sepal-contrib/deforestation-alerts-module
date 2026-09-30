"""Geo helpers copied from spatial-risk-module (srm/spatialrisk/geo_utils.py,
utilities/resources.py). Pure functions: no GEE, no UI."""


def get_utm_proj_str_from_lat_lon(lon, lat):
    """Return the EPSG string of the UTM/UPS zone containing (lon, lat) in WGS84."""
    if lat >= 84:
        return "EPSG:5041"  # UPS North
    if lat <= -80:
        return "EPSG:5042"  # UPS South

    # Norway / Svalbard exceptions
    if 55 < lat < 64 and 2 < lon < 6:
        zone_number = 32
    elif lat > 71 and 6 <= lon < 9:
        zone_number = 31
    elif lat > 71 and ((9 <= lon < 12) or (18 <= lon < 21)):
        zone_number = 33
    elif lat > 71 and ((21 <= lon < 24) or (30 <= lon < 33)):
        zone_number = 35
    else:
        zone_number = int((lon + 180) / 6) + 1

    epsg_code = (32600 if lat >= 0 else 32700) + zone_number
    return f"EPSG:{epsg_code}"


def optimal_block_shape(ny, nx, nband, ram_gb, ram_fraction=0.7, bytes_per_value=4):
    """Return (blk_rows, blk_cols) for memory-bounded sequential reading.

    ``bytes_per_value`` is the raster's itemsize: the alert export is float64
    (8) once the alert band is Int32, float32 (4) before.
    """
    target_gb = ram_gb * ram_fraction
    bytes_per_row = nx * nband * bytes_per_value
    max_rows = int((target_gb * 1024**3) / bytes_per_row)
    max_rows = max(1, min(ny, max_rows))
    return max_rows, nx

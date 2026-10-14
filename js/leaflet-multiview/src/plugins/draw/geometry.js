// Geometry for the draw plugin's "Select area": which drawing features a box
// holds. Pure functions on GeoJSON geometries ([lng, lat] positions).

const within = ([lng, lat], b) => lng >= b.west && lng <= b.east && lat >= b.south && lat <= b.north;

// The outer ring of each part. A hole lies inside its shell, so the shells
// decide.
function shells(geometry) {
  if (geometry.type === "Polygon") return [geometry.coordinates[0]];
  if (geometry.type === "MultiPolygon") return geometry.coordinates.map(part => part && part[0]);
  return [];
}

// Whether ``geometry`` lies wholly inside ``bounds`` ({west, south, east,
// north}, edges included): every vertex of every part's outer ring, or the
// point itself. A shape the box only crosses is not inside, nor is a missing
// or empty geometry, nor a line (the drawing holds none).
export function geometryInside(geometry, bounds) {
  if (!geometry) return false;
  if (geometry.type === "Point") return Array.isArray(geometry.coordinates) && within(geometry.coordinates, bounds);
  const rings = shells(geometry);
  if (!rings.length) return false;
  return rings.every(ring => Array.isArray(ring) && ring.length > 0 && ring.every(p => within(p, bounds)));
}

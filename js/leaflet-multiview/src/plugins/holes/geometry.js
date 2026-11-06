// js/leaflet-multiview/src/plugins/holes/geometry.js
// Hole geometry for the holes plugin. Pure: GeoJSON in, new GeoJSON out
// (inputs are never mutated). Coordinates are [lng, lat].

export function pointInRing([x, y], ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function polygonsOf(geometry) {
  if (geometry.type === "Polygon") return [geometry.coordinates];
  if (geometry.type === "MultiPolygon") return geometry.coordinates;
  return [];
}

export function holeCount(geometry) {
  return polygonsOf(geometry).reduce((n, rings) => n + Math.max(rings.length - 1, 0), 0);
}

export function fillHoles(geometry) {
  if (geometry.type === "Polygon") return {...geometry, coordinates: [geometry.coordinates[0]]};
  if (geometry.type === "MultiPolygon") {
    return {...geometry, coordinates: geometry.coordinates.map(rings => [rings[0]])};
  }
  return geometry;
}

// The rings without the first hole that contains ``point``, or null.
function withoutHoleAt(rings, point) {
  const i = rings.findIndex((ring, k) => k > 0 && pointInRing(point, ring));
  return i < 0 ? null : rings.filter((_, k) => k !== i);
}

export function removeHoleAt(geometry, point) {
  if (geometry.type === "Polygon") {
    const rings = withoutHoleAt(geometry.coordinates, point);
    return rings ? {geometry: {...geometry, coordinates: rings}, removed: true} : {geometry, removed: false};
  }
  if (geometry.type === "MultiPolygon") {
    for (let p = 0; p < geometry.coordinates.length; p++) {
      const rings = withoutHoleAt(geometry.coordinates[p], point);
      if (rings) {
        const coordinates = geometry.coordinates.map((r, k) => (k === p ? rings : r));
        return {geometry: {...geometry, coordinates}, removed: true};
      }
    }
  }
  return {geometry, removed: false};
}

export function fillHolesInFeatures(features, index = null) {
  return features.map((f, i) =>
    (index === null || i === index) && f.geometry ? {...f, geometry: fillHoles(f.geometry)} : f);
}

export function removeHoleInFeatures(features, point) {
  for (let i = 0; i < features.length; i++) {
    const f = features[i];
    if (!f.geometry) continue;
    const {geometry, removed} = removeHoleAt(f.geometry, point);
    if (removed) {
      return {features: features.map((g, k) => (k === i ? {...g, geometry} : g)), removed: true, index: i};
    }
  }
  return {features, removed: false, index: -1};
}

import type { SearchZone, SearchZoneGeometry } from "@/lib/api";

export const WEEK_OPTIONS = [
  { value: 0, label: "Lun", fullLabel: "Lunes" },
  { value: 1, label: "Mar", fullLabel: "Martes" },
  { value: 2, label: "Mié", fullLabel: "Miércoles" },
  { value: 3, label: "Jue", fullLabel: "Jueves" },
  { value: 4, label: "Vie", fullLabel: "Viernes" },
  { value: 5, label: "Sáb", fullLabel: "Sábado" },
  { value: 6, label: "Dom", fullLabel: "Domingo" },
] as const;

export function zonesForProvince(zones: SearchZone[], provinceId: string): SearchZone[] {
  return zones.filter((zone) => zone.parent_id === provinceId);
}

export function pruneZoneSelection(
  selectedZoneIds: string[],
  zones: SearchZone[],
  selectedProvinceIds: string[],
): string[] {
  const allowedProvinces = new Set(selectedProvinceIds);
  const allowedZoneIds = new Set(
    zones.filter((zone) => zone.parent_id && allowedProvinces.has(zone.parent_id)).map((zone) => zone.id),
  );
  return selectedZoneIds.filter((zoneId) => allowedZoneIds.has(zoneId));
}

type NumericBbox = [number, number, number, number];

export type ZoneMapPath = {
  id: string;
  name: string;
  path: string;
};

export type ZoneMap = {
  viewBox: string;
  paths: ZoneMapPath[];
};

function numericBbox(value: unknown): NumericBbox | null {
  if (!Array.isArray(value) || value.length !== 4 || value.some((item) => typeof item !== "number" || !Number.isFinite(item))) {
    return null;
  }
  return [value[0] as number, value[1] as number, value[2] as number, value[3] as number];
}

function geometryRings(geometry: unknown): unknown[][] {
  if (!geometry || typeof geometry !== "object") return [];
  const value = geometry as { type?: unknown; coordinates?: unknown };
  if (value.type === "Polygon" && Array.isArray(value.coordinates)) {
    return value.coordinates.filter(Array.isArray);
  }
  if (value.type === "MultiPolygon" && Array.isArray(value.coordinates)) {
    return value.coordinates.flatMap((polygon) => Array.isArray(polygon) ? polygon.filter(Array.isArray) : []);
  }
  return [];
}

function pathForGeometry(
  geometry: unknown,
  project: (longitude: number, latitude: number) => [number, number],
): string {
  const commands: string[] = [];
  geometryRings(geometry).forEach((ring) => {
    let started = false;
    ring.forEach((coordinate) => {
      if (!Array.isArray(coordinate) || coordinate.length < 2) return;
      const [longitude, latitude] = coordinate;
      if (typeof longitude !== "number" || typeof latitude !== "number") return;
      const [x, y] = project(longitude, latitude);
      commands.push(`${started ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`);
      started = true;
    });
    if (started) commands.push("Z");
  });
  return commands.join(" ");
}

export function buildZoneMap(
  zones: SearchZone[],
  geometries: SearchZoneGeometry[],
): ZoneMap {
  const width = 1000;
  const height = 560;
  const padding = 24;
  const geometryById = new Map(geometries.map((geometry) => [geometry.id, geometry]));
  const drawable = zones.flatMap((zone) => {
    const geometry = geometryById.get(zone.id);
    const bbox = numericBbox(geometry?.bbox);
    return geometry && bbox ? [{ zone, geometry, bbox }] : [];
  });
  if (!drawable.length) return { viewBox: `0 0 ${width} ${height}`, paths: [] };

  const minX = Math.min(...drawable.map(({ bbox }) => bbox[0]));
  const minY = Math.min(...drawable.map(({ bbox }) => bbox[1]));
  const maxX = Math.max(...drawable.map(({ bbox }) => bbox[2]));
  const maxY = Math.max(...drawable.map(({ bbox }) => bbox[3]));
  const xRange = Math.max(maxX - minX, 0.000001);
  const yRange = Math.max(maxY - minY, 0.000001);
  const scale = Math.min((width - padding * 2) / xRange, (height - padding * 2) / yRange);
  const offsetX = (width - xRange * scale) / 2;
  const offsetY = (height - yRange * scale) / 2;
  const project = (longitude: number, latitude: number): [number, number] => [
    offsetX + (longitude - minX) * scale,
    offsetY + (maxY - latitude) * scale,
  ];

  return {
    viewBox: `0 0 ${width} ${height}`,
    paths: drawable.flatMap(({ zone, geometry }) => {
      const path = pathForGeometry(geometry.geojson, project);
      return path ? [{ id: zone.id, name: zone.name, path }] : [];
    }),
  };
}

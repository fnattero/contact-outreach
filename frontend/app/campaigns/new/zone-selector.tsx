"use client";

import { Button, Checkbox, Input } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  buildZoneMap,
  zonesForProvince,
  type ZoneMap,
} from "@/app/campaigns/new/campaign-form-helpers";
import { getSearchZoneGeometry, type SearchZone } from "@/lib/api";

type MapLoadState =
  | { status: "loading" }
  | { status: "ready"; map: ZoneMap }
  | { status: "error" };

type ZoneSelectorProps = {
  provinces: SearchZone[];
  zones: SearchZone[];
  selectedProvinceIds: string[];
  value?: string[];
  onChange?: (value: string[]) => void;
};

async function loadProvinceMap(provinceZones: SearchZone[]): Promise<ZoneMap> {
  const geometries = [];
  const batchSize = 8;
  for (let index = 0; index < provinceZones.length; index += batchSize) {
    const batch = provinceZones.slice(index, index + batchSize);
    geometries.push(...await Promise.all(batch.map((zone) => getSearchZoneGeometry(zone.id))));
  }
  return buildZoneMap(provinceZones, geometries);
}

function normalized(value: string): string {
  return value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("es-AR");
}

export function ZoneSelector({
  provinces,
  zones,
  selectedProvinceIds,
  value = [],
  onChange,
}: ZoneSelectorProps) {
  const [activeProvinceId, setActiveProvinceId] = useState("");
  const [query, setQuery] = useState("");
  const [maps, setMaps] = useState<Record<string, MapLoadState>>({});
  const loadingIds = useRef(new Set<string>());
  const requestedIds = useRef(new Set<string>());
  const mounted = useRef(true);

  const selectedProvinces = useMemo(
    () => selectedProvinceIds.flatMap((id) => {
      const province = provinces.find((item) => item.id === id);
      return province ? [province] : [];
    }),
    [provinces, selectedProvinceIds],
  );
  const effectiveActiveProvinceId = selectedProvinceIds.includes(activeProvinceId)
    ? activeProvinceId
    : selectedProvinceIds[0] ?? "";
  const activeProvince = selectedProvinces.find((province) => province.id === effectiveActiveProvinceId);
  const activeZones = activeProvince ? zonesForProvince(zones, activeProvince.id) : [];
  const selectedIds = new Set(value);
  const filteredZones = query.trim()
    ? activeZones.filter((zone) => normalized(zone.name).includes(normalized(query.trim())))
    : activeZones;
  const activeSelectedCount = activeZones.filter((zone) => selectedIds.has(zone.id)).length;

  useEffect(() => () => { mounted.current = false; }, []);

  const requestMap = useCallback(async (province: SearchZone, force = false) => {
    if ((!force && requestedIds.current.has(province.id)) || loadingIds.current.has(province.id)) return;
    requestedIds.current.add(province.id);
    loadingIds.current.add(province.id);
    setMaps((current) => ({ ...current, [province.id]: { status: "loading" } }));
    try {
      const map = await loadProvinceMap(zonesForProvince(zones, province.id));
      if (mounted.current) setMaps((current) => ({ ...current, [province.id]: { status: "ready", map } }));
    } catch {
      requestedIds.current.delete(province.id);
      if (mounted.current) setMaps((current) => ({ ...current, [province.id]: { status: "error" } }));
    } finally {
      loadingIds.current.delete(province.id);
    }
  }, [zones]);

  useEffect(() => {
    if (!activeProvince || maps[activeProvince.id]) return;
    const timer = window.setTimeout(() => { void requestMap(activeProvince); }, 0);
    return () => window.clearTimeout(timer);
  }, [activeProvince, maps, requestMap]);

  function toggleZone(zoneId: string) {
    onChange?.(selectedIds.has(zoneId) ? value.filter((id) => id !== zoneId) : [...value, zoneId]);
  }

  function selectAllActive() {
    const next = [...value];
    activeZones.forEach((zone) => { if (!selectedIds.has(zone.id)) next.push(zone.id); });
    onChange?.(next);
  }

  function clearActive() {
    const activeIds = new Set(activeZones.map((zone) => zone.id));
    onChange?.(value.filter((id) => !activeIds.has(id)));
  }

  if (!selectedProvinces.length) {
    return <div className="zone-selector__prompt">Elegí una provincia para ver sus zonas en el mapa y en la lista.</div>;
  }

  const mapState = activeProvince ? maps[activeProvince.id] : undefined;

  return (
    <div className="zone-selector">
      <div className="zone-selector__province-tabs" role="tablist" aria-label="Provincias seleccionadas">
        {selectedProvinces.map((province) => {
          const count = zonesForProvince(zones, province.id).filter((zone) => selectedIds.has(zone.id)).length;
          const active = province.id === activeProvince?.id;
          return (
            <button
              className={`zone-selector__province-tab${active ? " zone-selector__province-tab--active" : ""}`}
              key={province.id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => { setActiveProvinceId(province.id); setQuery(""); }}
            >
              {province.name} <span>{count}</span>
            </button>
          );
        })}
      </div>

      <div className="zone-selector__toolbar">
        <p><strong>{activeProvince?.name}</strong> · {activeSelectedCount} de {activeZones.length} zonas seleccionadas</p>
        <div className="zone-selector__toolbar-actions">
          <Button type="link" onClick={selectAllActive}>Seleccionar todas</Button>
          {activeSelectedCount ? <Button type="link" onClick={clearActive}>Limpiar provincia</Button> : null}
        </div>
      </div>

      <div className="zone-selector__workspace">
        <div className="zone-map" aria-label={`Mapa de zonas de ${activeProvince?.name ?? "la provincia"}`}>
          {mapState?.status === "loading" || !mapState ? (
            <div className="zone-map__state" role="status">Cargando mapa oficial…</div>
          ) : mapState.status === "error" ? (
            <div className="zone-map__state" role="alert">
              <p>No se pudo cargar el mapa. Podés completar la selección desde la lista.</p>
              {activeProvince ? <Button onClick={() => void requestMap(activeProvince, true)}>Reintentar mapa</Button> : null}
            </div>
          ) : mapState.map.paths.length ? (
            <svg viewBox={mapState.map.viewBox} role="img" aria-label={`Mapa seleccionable de ${activeProvince?.name ?? "la provincia"}`}>
              {mapState.map.paths.map((zone) => {
                const selected = selectedIds.has(zone.id);
                return (
                  <path
                    key={zone.id}
                    className={`zone-map__shape${selected ? " zone-map__shape--selected" : ""}`}
                    d={zone.path}
                    fillRule="evenodd"
                    tabIndex={0}
                    role="button"
                    aria-pressed={selected}
                    aria-label={`${selected ? "Quitar" : "Seleccionar"} ${zone.name}`}
                    onClick={() => toggleZone(zone.id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        toggleZone(zone.id);
                      }
                    }}
                  >
                    <title>{zone.name}</title>
                  </path>
                );
              })}
            </svg>
          ) : (
            <div className="zone-map__state">Esta provincia no tiene geometrías disponibles para mostrar.</div>
          )}
        </div>

        <div className="zone-list" aria-label={`Lista de zonas de ${activeProvince?.name ?? "la provincia"}`}>
          <label className="zone-list__search-label" htmlFor="zone-search">Buscar una zona</label>
          <Input
            id="zone-search"
            allowClear
            value={query}
            placeholder="Escribí un nombre"
            onChange={(event) => setQuery(event.target.value)}
          />
          <div className="zone-list__options" role="group" aria-label="Zonas disponibles">
            {filteredZones.length ? filteredZones.map((zone) => (
              <Checkbox key={zone.id} checked={selectedIds.has(zone.id)} onChange={() => toggleZone(zone.id)}>
                {zone.name}
              </Checkbox>
            )) : <p className="zone-list__empty">No hay zonas que coincidan con la búsqueda.</p>}
          </div>
        </div>
      </div>
      <p className="zone-selector__help">El mapa y la lista controlan la misma selección. Cada zona elegida se combinará con los rubros de la campaña.</p>
    </div>
  );
}

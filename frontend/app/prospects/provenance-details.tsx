import type { Provenance } from "@/lib/api";
import { contactSourceLabel } from "./prospect-helpers";

/** Where a record came from and which licences its data carries (Overture requires attribution). */
export function ProvenanceDetails({ provenance }: { provenance: Provenance | null }) {
  if (!provenance) {
    return <p className="muted">Este registro no proviene de Overture, así que no tiene procedencia para mostrar.</p>;
  }
  const rule = provenance.matched_rule;
  return (
    <dl className="provenance-details">
      <dt>Datos fijados</dt>
      <dd>{provenance.release_id ? `Overture ${provenance.release_id}` : "Versión no informada"}</dd>
      <dt>Identificador GERS</dt>
      <dd><code>{provenance.overture_id ?? "No conservado en este registro anterior"}</code></dd>
      {provenance.confidence ? <><dt>Confianza de existencia</dt><dd>{provenance.confidence}</dd></> : null}
      <dt>Regla coincidente</dt>
      <dd>
        {rule && (rule.taxonomy_code || rule.name_terms.length)
          ? [rule.taxonomy_code && `Taxonomía ${rule.taxonomy_code}`, rule.name_terms.length && `Términos: ${rule.name_terms.join(", ")}`].filter(Boolean).join(" · ")
          : "Este registro no conserva una regla estructurada."}
      </dd>
      <dt>Origen del contacto</dt>
      <dd>
        {provenance.contact_source
          ? <>{provenance.contact_source.email} · {contactSourceLabel(provenance.contact_source.source)}{provenance.contact_source.source_url ? <> · <code>{provenance.contact_source.source_url}</code></> : null}</>
          : "No se seleccionó un correo."}
      </dd>
      {provenance.attribution.length || provenance.licenses.length ? (
        <>
          <dt>Atribución y licencias</dt>
          <dd>
            {provenance.attribution.map((line) => <p key={line}>{line}</p>)}
            {provenance.licenses.length ? <ul>{provenance.licenses.map((license) => <li key={license}>{license}</li>)}</ul> : null}
          </dd>
        </>
      ) : null}
    </dl>
  );
}

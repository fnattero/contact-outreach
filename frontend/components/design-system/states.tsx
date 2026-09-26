"use client";

import { ArrowRightOutlined, ReloadOutlined, StopOutlined } from "@ant-design/icons";
import { Button } from "antd";
import { motion } from "framer-motion";
import Link from "next/link";
import type { ReactNode } from "react";
import { motion as motionTokens } from "@/src/theme/tokens";

type LoadingLayout = "shell" | "list" | "detail" | "form";

export type LoadingStateProps = {
  loading?: boolean;
  layout?: LoadingLayout;
  children?: ReactNode;
  label?: string;
};

const rowCounts: Record<LoadingLayout, number> = {
  shell: 5,
  list: 6,
  detail: 4,
  form: 4,
};

export function LoadingState({ loading = true, layout = "list", children, label = "Cargando contenido" }: LoadingStateProps) {
  if (!loading) {
    return (
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: motionTokens.base }}>
        {children}
      </motion.div>
    );
  }

  return (
    <div className={`loading-state loading-state--${layout}`} role="status" aria-label={label} aria-live="polite">
      <span className="visually-hidden">{label}</span>
      <div className="loading-state__heading" />
      <div className="loading-state__subheading" />
      <div className="loading-state__body">
        {Array.from({ length: rowCounts[layout] }, (_, index) => (
          <div className="loading-state__row" key={index} />
        ))}
      </div>
    </div>
  );
}

function EmptyMark() {
  return (
    <svg className="empty-state__mark" viewBox="0 0 48 48" fill="none" aria-hidden>
      <path d="M12 14.5h24v24H12zM17 9.5h14M17 21h14M17 27h10" />
      <path d="m29 34 4 4 7-8" />
    </svg>
  );
}

export type EmptyStateProps = {
  headline: string;
  explanation: string;
  actionLabel: string;
  headingId?: string;
} & (
  | { actionHref: string; onAction?: never }
  | { actionHref?: never; onAction: () => void }
);

export function EmptyState({ headline, explanation, actionLabel, actionHref, onAction, headingId = "empty-state-heading" }: EmptyStateProps) {
  return (
    <section className="state-panel" aria-labelledby={headingId}>
      <EmptyMark />
      <h2 className="type-title" id={headingId}>{headline}</h2>
      <p>{explanation}</p>
      <Button className="state-panel__action" type="primary" href={actionHref} onClick={onAction}>
        {actionLabel}
      </Button>
    </section>
  );
}

export type ErrorStateProps = {
  failed: string;
  instruction: string;
  onRetry: () => void;
  retryLabel?: string;
};

export function ErrorState({ failed, instruction, onRetry, retryLabel = "Reintentar" }: ErrorStateProps) {
  return (
    <section className="state-panel state-panel--error" aria-labelledby="error-state-heading" role="alert">
      <StopOutlined className="state-panel__icon" aria-hidden />
      <h2 className="type-title" id="error-state-heading">{failed}</h2>
      <p>{instruction}</p>
      <Button type="primary" icon={<ReloadOutlined />} onClick={onRetry}>
        {retryLabel}
      </Button>
    </section>
  );
}

export type ForbiddenStateProps = {
  resource?: string;
  destination?: string;
};

export function ForbiddenState({ resource = "esta sección", destination = "/dashboard" }: ForbiddenStateProps) {
  return (
    <section className="state-panel state-panel--forbidden" aria-labelledby="forbidden-state-heading">
      <StopOutlined className="state-panel__icon" aria-hidden />
      <h2 className="type-title" id="forbidden-state-heading">No tenés acceso a {resource}</h2>
      <p>Tu rol no permite consultar esta información. Volvé al resumen para continuar.</p>
      <Link href={destination}>
        <span className="state-panel__link">Ir al resumen <ArrowRightOutlined aria-hidden /></span>
      </Link>
    </section>
  );
}

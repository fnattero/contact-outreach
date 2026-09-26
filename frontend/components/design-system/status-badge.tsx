"use client";

import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  InfoCircleOutlined,
  MinusCircleOutlined,
} from "@ant-design/icons";
import type { ReactNode } from "react";

export type SemanticLevel = "success" | "warning" | "danger" | "inactive" | "info";

export type DisplayValueDefinition = {
  label: string;
  explanation?: string;
  level: SemanticLevel;
  technicalValueOnly?: boolean;
  hiddenFromPrimary?: boolean;
};

export const displayValueMap = {
  "dry-run": {
    label: "Simulación",
    explanation: "Se prepara todo pero no se envía ningún email.",
    level: "inactive",
  },
  live: {
    label: "Envío real",
    explanation: "Los emails salen de verdad a los contactos.",
    level: "warning",
  },
  LIVE: {
    label: "Envío real",
    explanation: "Los emails salen de verdad a los contactos.",
    level: "warning",
  },
  SHADOW: {
    label: "Observación",
    explanation: "El sistema redacta respuestas pero no las envía.",
    level: "info",
  },
  OFF: {
    label: "Desactivada",
    explanation: "El sistema no redacta ni envía respuestas.",
    level: "inactive",
  },
  DISCONNECTED: { label: "Sin conectar", level: "inactive" },
  CONNECTED: { label: "Conectada", level: "success" },
  bloqueado: { label: "Detenido", level: "inactive" },
  bloqueadas: { label: "Detenido", level: "inactive" },
  fake: { label: "Sin proveedor real configurado", level: "inactive", technicalValueOnly: true },
  "fake-deterministic": {
    label: "Sin proveedor real configurado",
    level: "inactive",
    technicalValueOnly: true,
  },
  "Confianza mínima: 0.750": { label: "Confianza mínima: 75%", level: "info" },
  "text-embedding-3-small": { label: "", level: "inactive", hiddenFromPrimary: true },
} as const satisfies Record<string, DisplayValueDefinition>;

export type DisplayValue = keyof typeof displayValueMap;
type StatusDisplayValue = Exclude<DisplayValue, "Confianza mínima: 0.750" | "text-embedding-3-small">;

const levelIcons: Record<SemanticLevel, ReactNode> = {
  success: <CheckCircleOutlined aria-hidden />,
  warning: <ExclamationCircleOutlined aria-hidden />,
  danger: <CloseCircleOutlined aria-hidden />,
  inactive: <MinusCircleOutlined aria-hidden />,
  info: <InfoCircleOutlined aria-hidden />,
};

type MappedStatusBadgeProps = {
  value: StatusDisplayValue;
  label?: never;
  level?: never;
};

type ExplicitStatusBadgeProps = {
  value?: never;
  label: string;
  level: SemanticLevel;
};

export type StatusBadgeProps = (MappedStatusBadgeProps | ExplicitStatusBadgeProps) & {
  className?: string;
};

export function StatusBadge(props: StatusBadgeProps) {
  const definition = props.value ? displayValueMap[props.value] : { label: props.label, level: props.level };

  return (
    <span className={`status-badge status-badge--${definition.level}${props.className ? ` ${props.className}` : ""}`}>
      {levelIcons[definition.level]}
      <span>{definition.label}</span>
    </span>
  );
}

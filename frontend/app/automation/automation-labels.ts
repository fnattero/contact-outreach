import type { AutomationConfiguration } from "@/lib/api";
import type { SemanticLevel } from "@/components/design-system/status-badge";

type Mode = AutomationConfiguration["mode"];

/** What each internal mode is called and means for the person using the app. */
export const AUTOMATION_STATES: ReadonlyArray<{
  value: Mode;
  title: string;
  level: SemanticLevel;
  explanation: string;
}> = [
  {
    value: "OFF",
    title: "Apagadas",
    level: "inactive",
    explanation: "Nadie contesta por vos: los correos que llegan quedan para que los atiendas.",
  },
  {
    value: "SHADOW",
    title: "Practicando",
    level: "info",
    explanation: "Prepara respuestas para que las veas, pero no envía nada.",
  },
  {
    value: "LIVE",
    title: "Encendidas",
    level: "warning",
    explanation:
      "Contesta sola las preguntas simples que están permitidas. Todo lo demás te queda en Necesita atención.",
  },
];

export function automationState(mode: Mode) {
  return AUTOMATION_STATES.find((state) => state.value === mode) ?? AUTOMATION_STATES[0];
}

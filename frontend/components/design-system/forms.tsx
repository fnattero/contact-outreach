"use client";

import { Button } from "antd";
import { AnimatePresence, motion } from "framer-motion";
import type { ReactNode } from "react";
import { DisabledReason } from "@/components/design-system/disabled-reason";
import { motion as motionTokens } from "@/src/theme/tokens";

export type FormSectionProps = {
  title: string;
  description?: string;
  children: ReactNode;
};

export function FormSection({ title, description, children }: FormSectionProps) {
  return (
    <fieldset className="form-section">
      <legend className="type-title">{title}</legend>
      {description ? <p className="form-section__description">{description}</p> : null}
      <div className="form-section__fields">{children}</div>
    </fieldset>
  );
}

type SaveFeedback =
  | { state: "idle" | "saving"; message?: string }
  | { state: "saved" | "error"; message: string };

export type StickySaveBarProps = {
  dirty: boolean;
  feedback?: SaveFeedback;
  onSave: () => void;
  onCancel: () => void;
};

export function StickySaveBar({
  dirty,
  feedback = { state: "idle" },
  onSave,
  onCancel,
}: StickySaveBarProps) {
  const saving = feedback.state === "saving";

  return (
    <AnimatePresence>
      {dirty ? (
        <motion.div
          className="sticky-save-bar"
          role="region"
          aria-label="Cambios sin guardar"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 8 }}
          transition={{ duration: motionTokens.base }}
        >
          <span className={`sticky-save-bar__feedback sticky-save-bar__feedback--${feedback.state}`} aria-live="polite">
            {feedback.message ?? (saving ? "Guardando cambios…" : "Hay cambios sin guardar.")}
          </span>
          <div className="sticky-save-bar__actions">
            <Button onClick={onCancel}>Cancelar</Button>
            <DisabledReason disabled={saving} reason="Los cambios se están guardando.">
              <Button type="primary" onClick={onSave}>{saving ? "Guardando…" : "Guardar"}</Button>
            </DisabledReason>
          </div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

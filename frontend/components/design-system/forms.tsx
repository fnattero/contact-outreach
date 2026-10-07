"use client";

import { DownOutlined } from "@ant-design/icons";
import { Button } from "antd";
import { AnimatePresence, motion } from "framer-motion";
import { useId, useState, type ReactNode } from "react";
import { DisabledReason } from "@/components/design-system/disabled-reason";
import { motion as motionTokens } from "@/src/theme/tokens";

export type FormSectionProps = {
  title: string;
  description?: string;
  children: ReactNode;
  /** Sections start open; the person can fold the ones they are not working on. */
  defaultOpen?: boolean;
};

export function FormSection({ title, description, children, defaultOpen = true }: FormSectionProps) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId().replaceAll(":", "");
  return (
    <section className={`form-section${open ? "" : " form-section--collapsed"}`} aria-labelledby={`${id}-title`}>
      <h2 className="form-section__heading" id={`${id}-title`}>
        <button
          type="button"
          className="form-section__toggle"
          aria-expanded={open}
          aria-controls={`${id}-body`}
          onClick={() => setOpen((current) => !current)}
        >
          <span className="type-title">{title}</span>
          <DownOutlined aria-hidden className="form-section__chevron" />
        </button>
      </h2>
      {/* Kept mounted while folded so form fields keep their values and validation. */}
      <div id={`${id}-body`} className="form-section__body" hidden={!open}>
        {description ? <p className="form-section__description">{description}</p> : null}
        <div className="form-section__fields">{children}</div>
      </div>
    </section>
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

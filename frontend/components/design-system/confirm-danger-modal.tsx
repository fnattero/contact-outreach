"use client";

import { Button, Input, Modal } from "antd";
import { useRef, useState } from "react";
import { DisabledReason } from "@/components/design-system/disabled-reason";

export type ConfirmDangerModalProps = {
  open: boolean;
  title: string;
  consequences: readonly [string, ...string[]];
  confirmationWord: string;
  dangerLabel: string;
  safeLabel?: string;
  confirming?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
};

export function ConfirmDangerModal({
  open,
  title,
  consequences,
  confirmationWord,
  dangerLabel,
  safeLabel = "Cancelar",
  confirming = false,
  onCancel,
  onConfirm,
}: ConfirmDangerModalProps) {
  const [typedValue, setTypedValue] = useState("");
  const safeButtonRef = useRef<HTMLButtonElement>(null);
  const confirmed = typedValue === confirmationWord;

  return (
    <Modal
      open={open}
      title={title}
      onCancel={onCancel}
      closable={!confirming}
      maskClosable={false}
      keyboard={!confirming}
      afterOpenChange={(isOpen) => {
        if (isOpen) {
          safeButtonRef.current?.focus();
        } else {
          setTypedValue("");
        }
      }}
      footer={
        <div className="danger-modal__footer">
          <DisabledReason disabled={confirming} reason="La acción está en curso.">
            <Button ref={safeButtonRef} onClick={onCancel}>{safeLabel}</Button>
          </DisabledReason>
          <DisabledReason
            disabled={!confirmed || confirming}
            reason={confirming ? "La acción está en curso." : `Escribí ${confirmationWord} para confirmar.`}
          >
            <Button danger type="primary" onClick={onConfirm}>
              {confirming ? "Confirmando…" : dangerLabel}
            </Button>
          </DisabledReason>
        </div>
      }
    >
      <p>Esta acción tiene estas consecuencias:</p>
      <ul className="danger-modal__consequences">
        {consequences.map((consequence) => <li key={consequence}>{consequence}</li>)}
      </ul>
      <label className="danger-modal__label" htmlFor="danger-confirmation">
        Escribí <strong>{confirmationWord}</strong> para confirmar
      </label>
      <Input
        id="danger-confirmation"
        autoComplete="off"
        value={typedValue}
        onChange={(event) => setTypedValue(event.target.value)}
      />
    </Modal>
  );
}

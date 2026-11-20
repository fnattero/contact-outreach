"use client";

import { Alert, Button, Input, Modal } from "antd";
import { useState } from "react";
import { ConfirmDangerModal } from "@/components/design-system/confirm-danger-modal";
import { problemMessage, reauthenticate, type Problem } from "@/lib/api";

// The API asks for this word before it erases anything.
const CONFIRMATION_WORD = "ELIMINAR";

type Props = {
  // Calls the erase endpoint with the typed word.
  erase: (confirmation: string) => Promise<unknown>;
  // Reload the page after the erasure so it shows what is left.
  onErased: () => void;
  size?: "small" | "middle";
  // A shorter label fits a table row; the dialog still names the business-wide effect.
  label?: string;
};

/** Erases a business's data on request, keeping only what is needed to never write to it again. */
export function EraseBusinessControl({
  erase,
  onErased,
  size = "middle",
  label = "Eliminar datos de este negocio",
}: Props) {
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  function closeAll() {
    setPasswordOpen(false);
    setConfirmOpen(false);
    setPassword("");
  }

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await reauthenticate(password);
      await erase(CONFIRMATION_WORD);
      closeAll();
      onErased();
    } catch (problem) {
      setError(problem);
      setConfirmOpen(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button size={size} danger onClick={() => setPasswordOpen(true)}>
        {label}
      </Button>
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <Modal
        open={passwordOpen && !confirmOpen}
        title="Eliminar datos de este negocio"
        okText="Continuar"
        cancelText="Cancelar"
        okButtonProps={{ disabled: !password }}
        onOk={() => setConfirmOpen(true)}
        onCancel={closeAll}
      >
        <p>
          Usalo cuando el negocio te pida que borres sus datos. Ingresá tu contraseña otra vez para
          continuar.
        </p>
        <label className="send-mode-password">
          <span>Contraseña actual</span>
          <Input.Password
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
      </Modal>
      <ConfirmDangerModal
        open={confirmOpen}
        title="Eliminar los datos de este negocio"
        consequences={[
          "Se borran su nombre, dirección, teléfono y web, el nombre de la persona de contacto, los textos de los correos enviados y recibidos, las notas, las tareas y lo que analizó la IA.",
          "Se conserva solo su dirección de correo, para que nunca se le vuelva a escribir. Ese bloqueo no se puede quitar, y si una búsqueda lo vuelve a encontrar no se guarda nada.",
          "Se cancela todo lo que estaba por enviarse. Los correos que siguen en tu Gmail tenés que borrarlos vos desde Gmail.",
          "No se puede deshacer.",
        ]}
        confirmationWord={CONFIRMATION_WORD}
        dangerLabel="Eliminar datos"
        confirming={busy}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => void confirm()}
      />
    </>
  );
}

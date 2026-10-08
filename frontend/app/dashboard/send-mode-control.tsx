"use client";

import { Alert, Button, Input, Modal } from "antd";
import { useState } from "react";
import { ConfirmDangerModal } from "@/components/design-system/confirm-danger-modal";
import { problemMessage, reauthenticate, setSendLive, type DashboardSummary, type Problem } from "@/lib/api";

// The API asks the administrator to type this word before real email can leave.
const CONFIRMATION_WORD = "CONFIRMAR";

type Props = {
  safety: DashboardSummary["safety"];
  // Reload the summary after a change so every label follows the new state.
  onChanged: () => void;
};

/** Switches between simulation and real sending, when the server permits real sending. */
export function SendModeControl({ safety, onChanged }: Props) {
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function run(action: () => Promise<unknown>): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged();
      return true;
    } catch (problem) {
      setError(problem);
      return false;
    } finally {
      setBusy(false);
    }
  }

  function closeAll() {
    setPasswordOpen(false);
    setConfirmOpen(false);
    setPassword("");
  }

  async function turnOn() {
    const done = await run(async () => {
      await reauthenticate(password);
      await setSendLive("enable-live", CONFIRMATION_WORD);
    });
    if (done) closeAll();
    else setConfirmOpen(false);
  }

  const errorAlert = error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null;

  if (safety.send_effective_live) {
    return (
      <>
        <Button size="small" loading={busy} onClick={() => void run(() => setSendLive("disable-live"))}>
          Volver a simulación
        </Button>
        {errorAlert}
      </>
    );
  }
  if (!safety.send_server_allows_live) {
    return (
      <span className="send-mode-note">
        El servidor todavía no permite envíos reales. Quien lo administra tiene que habilitarlos en su
        configuración; después vas a poder encenderlos desde acá.
      </span>
    );
  }
  return (
    <>
      <Button size="small" type="primary" danger onClick={() => setPasswordOpen(true)}>
        Pasar a envío real
      </Button>
      {errorAlert}
      <Modal
        open={passwordOpen && !confirmOpen}
        title="Pasar a envío real"
        okText="Continuar"
        cancelText="Cancelar"
        okButtonProps={{ disabled: !password }}
        onOk={() => setConfirmOpen(true)}
        onCancel={closeAll}
      >
        <p>Ingresá tu contraseña otra vez para poder encender los envíos reales.</p>
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
        title="Encender los envíos reales"
        consequences={[
          "Las campañas que apruebes van a enviar correos de verdad desde tu Gmail.",
          "Los bloqueos del servidor, el horario, los límites y las restricciones se siguen revisando antes de cada envío.",
          "Podés volver a simulación cuando quieras; lo ya enviado no se revierte.",
        ]}
        confirmationWord={CONFIRMATION_WORD}
        dangerLabel="Encender envíos reales"
        confirming={busy}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => void turnOn()}
      />
    </>
  );
}

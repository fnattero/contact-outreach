"use client";

import { Alert, Button, Collapse, Flex, Form, Input, Radio, Tag } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { ConfirmDangerModal } from "@/components/design-system/confirm-danger-modal";
import { PageHeader } from "@/components/design-system/page-header";
import { SectionTabs } from "@/components/design-system/section-tabs";
import { LoadingState } from "@/components/design-system/states";
import { StatusBadge } from "@/components/design-system/status-badge";
import { AUTOMATION_TABS } from "@/components/design-system/tabs-config";
import {
  can,
  getAutomationConfiguration,
  getDashboardSummary,
  problemMessage,
  reauthenticate,
  setAutomationLive,
  updateAutomationMode,
  type AutomationConfiguration,
  type DashboardSummary,
  type Problem,
} from "@/lib/api";
import { AUTOMATION_STATES, automationState } from "./automation-labels";

// The API requires this exact word to turn the replies on; the modal asks the admin to type it.
const CONFIRMATION_WORD = "CONFIRMAR";

type Mode = AutomationConfiguration["mode"];

export default function AutomationPage() {
  const { session } = useAuth();
  const allowed = can(session, "manage_automation");
  const [configuration, setConfiguration] = useState<AutomationConfiguration | null>(null);
  const [safety, setSafety] = useState<DashboardSummary["safety"] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [choice, setChoice] = useState<Mode | null>(null);
  const [password, setPassword] = useState("");
  const [turnOnOpen, setTurnOnOpen] = useState(false);
  const [leaveLiveOpen, setLeaveLiveOpen] = useState(false);

  useEffect(() => {
    if (!allowed) return;
    void getAutomationConfiguration().then(setConfiguration).catch(setError);
    // Only used to explain why nothing is sent; the page works without it.
    void getDashboardSummary().then((summary) => setSafety(summary.safety)).catch(() => undefined);
  }, [allowed]);

  if (!allowed) return <AuthError error={{ detail: "No tenés permisos para configurar automatización." }} />;
  if (error && !configuration) return <AuthError error={error} />;
  if (!configuration) return <LoadingState layout="form" />;

  const current = automationState(configuration.mode);
  const selected: Mode = choice ?? configuration.mode;
  const changed = selected !== configuration.mode;
  const turningOn = changed && selected === "LIVE";
  const leavingLive = changed && configuration.mode === "LIVE";

  async function run(action: () => Promise<AutomationConfiguration>) {
    setSaving(true);
    setError(null);
    try {
      setConfiguration(await action());
      setChoice(null);
      setPassword("");
      return true;
    } catch (problem) {
      setError(problem);
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function save() {
    if (turningOn) {
      setTurnOnOpen(true);
    } else if (leavingLive) {
      setLeaveLiveOpen(true);
    } else if (changed && (selected === "OFF" || selected === "SHADOW")) {
      await run(() => updateAutomationMode(selected));
    }
  }

  async function turnOn() {
    const done = await run(async () => {
      await reauthenticate(password);
      return setAutomationLive("enable-live", CONFIRMATION_WORD);
    });
    if (done) setTurnOnOpen(false);
  }

  async function leaveLive() {
    const done = await run(() =>
      selected === "OFF" ? updateAutomationMode("OFF") : setAutomationLive("disable-live"),
    );
    if (done) setLeaveLiveOpen(false);
  }

  const blockedBy: string[] = [];
  if (safety && (safety.send_mode !== "live" || safety.send_kill_switch)) {
    blockedBy.push("el servidor está en modo simulación o con los envíos bloqueados");
  }
  if (safety?.auto_reply_kill_switch) blockedBy.push("las respuestas automáticas están bloqueadas desde el servidor");

  return (
    <Flex vertical gap="large">
      <PageHeader
        title="Respuestas automáticas"
        description="Decidí si la app contesta sola los correos que llegan, cómo escribe y qué información puede usar."
        tabs={<SectionTabs label="Respuestas automáticas" tabs={AUTOMATION_TABS} />}
      />
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      <div className="form-column integration-stack">
      <section className={`automation-mode-block automation-mode-block--${configuration.mode.toLowerCase()}`} aria-labelledby="automation-mode-heading">
        <div className="automation-mode-block__heading">
          <span className="type-micro">Estado actual</span>
          <h2 className="type-title" id="automation-mode-heading">Las respuestas automáticas están {current.title.toLowerCase()}</h2>
          <StatusBadge label={current.title} level={current.level} />
        </div>
        <p className="automation-mode-block__meaning">{current.explanation}</p>
      </section>
      {blockedBy.length ? (
        <Alert
          type="warning"
          showIcon
          message="Por ahora no sale ningún correo real"
          description={`Aunque las enciendas, no se envía nada porque ${blockedBy.join(" y ")}. Para cambiarlo hay que modificar la configuración del servidor.`}
        />
      ) : null}
        <Form layout="vertical" onFinish={() => void save()}>
          <Form.Item label="¿Qué querés que haga la app con los correos que llegan?">
            <Radio.Group className="campaign-choice-group" value={selected} onChange={(event) => setChoice(event.target.value as Mode)}>
              {AUTOMATION_STATES.map((state) => (
                <Radio key={state.value} value={state.value}>
                  <span>
                    <strong>{state.title}</strong>
                    <small>{state.explanation}</small>
                  </span>
                </Radio>
              ))}
            </Radio.Group>
          </Form.Item>
          {turningOn ? (
            <>
              <Alert
                type="warning"
                showIcon
                message="Acción sensible"
                description="Ingresá tu contraseña otra vez. Encenderlas no desactiva ninguno de los bloqueos de seguridad, ni permite respuestas fuera de la política aprobada."
              />
              <Form.Item label="Contraseña actual" htmlFor="automation-password" style={{ marginTop: 16 }}>
                <Input.Password
                  id="automation-password"
                  autoComplete="new-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              </Form.Item>
            </>
          ) : null}
          <Button
            type="primary"
            danger={turningOn}
            htmlType="submit"
            loading={saving}
            disabled={!changed || (turningOn && !password)}
          >
            {turningOn ? "Revisar y encender" : "Guardar cambio"}
          </Button>
        </Form>
      </div>
      <Collapse
        items={[
          {
            key: "safety",
            label: "Ver detalles de seguridad",
            children: (
              <>
                <p className="integration-muted">Estos controles los revisa el servidor antes de cada envío. Esta pantalla no los modifica.</p>
                {safety ? (
                  <div className="automation-safety-grid">
                    <div><span className="type-micro">Envíos</span><StatusBadge label={safety.send_kill_switch ? "Protegidos" : "Habilitados"} level={safety.send_kill_switch ? "success" : "danger"} /><p>{safety.send_kill_switch ? "El bloqueo impide cualquier envío." : "El bloqueo permite envíos si las demás condiciones se cumplen."}</p></div>
                    <div><span className="type-micro">Respuestas automáticas</span><StatusBadge label={safety.auto_reply_kill_switch ? "Detenidas" : "Habilitadas"} level={safety.auto_reply_kill_switch ? "inactive" : "warning"} /><p>{safety.auto_reply_kill_switch ? "No se preparan ni se envían respuestas automáticas." : "Se pueden preparar según la configuración vigente."}</p></div>
                    <div><span className="type-micro">Relaciones</span><StatusBadge label={safety.relationship_kill_switch ? "Protegidas" : "Habilitadas"} level={safety.relationship_kill_switch ? "success" : "warning"} /><p>{safety.relationship_kill_switch ? "Los mensajes programados a contactos están bloqueados." : "Los mensajes programados siguen las políticas vigentes."}</p></div>
                  </div>
                ) : null}
                <Flex gap="small" wrap>
                  <Tag>Política {configuration.policy_version}</Tag>
                  {configuration.live_enabled_by ? <Tag>Encendidas por {configuration.live_enabled_by}</Tag> : null}
                </Flex>
              </>
            ),
          },
        ]}
      />
      {leaveLiveOpen ? (
        <ConfirmDangerModal
          open
          title="Dejar de contestar automáticamente"
          consequences={[
            selected === "OFF"
              ? "Las respuestas automáticas se apagan: los correos que lleguen quedan para que los atiendas."
              : "Las respuestas pasan a prepararse sin enviarse: vas a poder verlas, pero no salen.",
            "Las respuestas que ya se enviaron no se revierten.",
          ]}
          confirmationWord={CONFIRMATION_WORD}
          dangerLabel="Dejar de contestar"
          confirming={saving}
          onCancel={() => setLeaveLiveOpen(false)}
          onConfirm={() => void leaveLive()}
        />
      ) : null}
      <ConfirmDangerModal
        open={turnOnOpen}
        title="Encender las respuestas automáticas"
        consequences={[
          "La app va a contestar sola las preguntas simples que estén permitidas por la política.",
          "Esas respuestas pueden salir de tu Gmail de verdad.",
          "Antes de cada una se vuelve a comprobar la política, Gmail, las restricciones y las tareas de revisión pendientes.",
        ]}
        confirmationWord={CONFIRMATION_WORD}
        dangerLabel="Encender respuestas"
        confirming={saving}
        onCancel={() => setTurnOnOpen(false)}
        onConfirm={() => void turnOn()}
      />
    </Flex>
  );
}

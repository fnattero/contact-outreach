"use client";

import { Alert, Button, Card, Drawer, Flex, Form, Input, Modal, Select, Table } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import { ConfirmDangerModal } from "@/components/design-system/confirm-danger-modal";
import { DisabledReason } from "@/components/design-system/disabled-reason";
import { FormSection, StickySaveBar } from "@/components/design-system/forms";
import { PageHeader } from "@/components/design-system/page-header";
import { LoadingState } from "@/components/design-system/states";
import { StatusBadge } from "@/components/design-system/status-badge";
import { createUser, getUsers, problemMessage, updateUserRole, updateUserStatus, type CreatedUser, type ManagedUser, type Problem } from "@/lib/api";

type UserForm = { username: string; email: string; role: ManagedUser["role"] };

export default function UsersSettingsPage() {
  const { session } = useAuth();
  const [form] = Form.useForm<UserForm>();
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [created, setCreated] = useState<CreatedUser | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [createDirty, setCreateDirty] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [roleDrafts, setRoleDrafts] = useState<Record<number, ManagedUser["role"]>>({});
  const [roleSaving, setRoleSaving] = useState<number | null>(null);
  const [deactivateTarget, setDeactivateTarget] = useState<ManagedUser | null>(null);

  function refresh() {
    setLoading(true);
    void getUsers().then((next) => { setUsers(next); setRoleDrafts(Object.fromEntries(next.map((user) => [user.id, user.role]))); }).catch(setError).finally(() => setLoading(false));
  }

  useEffect(() => { void getUsers().then((next) => { setUsers(next); setRoleDrafts(Object.fromEntries(next.map((user) => [user.id, user.role]))); }).catch(setError).finally(() => setLoading(false)); }, []);

  if (session?.role !== "ADMIN") return <AuthError error={{ detail: "No tenés permisos para administrar usuarios." }} />;
  if (error && !users.length && !loading) return <AuthError error={error} />;

  async function submit(values: UserForm) {
    setSaving(true); setError(null); setCreated(null);
    try { setCreated(await createUser(values)); setDrawerOpen(false); form.resetFields(); setCreateDirty(false); refresh(); }
    catch (problem) { setError(problem); }
    finally { setSaving(false); }
  }

  async function saveRole(user: ManagedUser) {
    const role = roleDrafts[user.id] ?? user.role;
    if (role === user.role) return;
    setRoleSaving(user.id); setError(null);
    try { const updated = await updateUserRole(user.id, role); setUsers((current) => current.map((item) => item.id === updated.id ? updated : item)); setRoleDrafts((current) => ({ ...current, [updated.id]: updated.role })); }
    catch (problem) { setError(problem); }
    finally { setRoleSaving(null); }
  }

  async function confirmDeactivate() {
    if (!deactivateTarget) return;
    setRoleSaving(deactivateTarget.id); setError(null);
    try { const updated = await updateUserStatus(deactivateTarget.id, false); setUsers((current) => current.map((item) => item.id === updated.id ? updated : item)); setDeactivateTarget(null); }
    catch (problem) { setError(problem); }
    finally { setRoleSaving(null); }
  }

  function closeCreate() { if (createDirty) setCancelOpen(true); else setDrawerOpen(false); }
  function discardCreate() { form.resetFields(); setCreateDirty(false); setCancelOpen(false); setDrawerOpen(false); }

  if (loading) return <LoadingState layout="list" />;
  return (
    <Flex vertical gap="large">
      <PageHeader title="Usuarios" description="El alta se completa mediante un enlace único de activación con vencimiento." primaryAction={<Button type="primary" onClick={() => setDrawerOpen(true)}>Crear usuario</Button>} />
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      {created ? <Alert type="success" showIcon message="Usuario creado. Copiá el enlace de activación ahora." description={<Flex vertical gap="small"><span>Vence: {new Date(created.expires_at).toLocaleString("es-AR")}</span><Input readOnly value={created.activation_url} aria-label="Enlace de activación" /></Flex>} /> : null}
      <Card title="Usuarios del espacio de trabajo">
        <Table<ManagedUser> rowKey="id" dataSource={users} pagination={{ pageSize: 10, responsive: true }} columns={[
          { title: "Usuario", dataIndex: "username", render: (value: string) => <strong>{value}</strong> },
          { title: "Email", dataIndex: "email", render: (value: string) => <span className="data-text">{value || "Sin email"}</span> },
          { title: "Rol", dataIndex: "role", render: (_: ManagedUser["role"], user) => <Flex align="center" gap="small"><Select value={roleDrafts[user.id] ?? user.role} onChange={(role: ManagedUser["role"]) => setRoleDrafts((current) => ({ ...current, [user.id]: role }))} options={[{ value: "VENDEDOR", label: "Vendedor/a" }, { value: "ADMIN", label: "Administrador/a" }]} aria-label={`Rol de ${user.username}`} /><DisabledReason disabled={(roleDrafts[user.id] ?? user.role) === user.role || roleSaving === user.id} reason={roleSaving === user.id ? "El cambio de rol se está guardando." : "Elegí un rol diferente para guardar."}><Button onClick={() => void saveRole(user)} loading={roleSaving === user.id}>Guardar rol</Button></DisabledReason></Flex> },
          { title: "Estado", dataIndex: "is_active", render: (value: boolean) => <StatusBadge label={value ? "Activo" : "Inactivo"} level={value ? "success" : "inactive"} /> },
          { title: "Última actividad", render: () => <span className="catalog-reference-unavailable">No disponible por la API</span> },
          { title: "Acción", render: (_: unknown, user) => <DisabledReason disabled={user.id === session.id} reason="No podés desactivar tu propio usuario."><Button danger={user.is_active} onClick={() => setDeactivateTarget(user)}>{user.is_active ? "Desactivar" : "Activar"}</Button></DisabledReason> },
        ]} />
      </Card>
      <Drawer title="Crear usuario" open={drawerOpen} onClose={closeCreate} width={520}>
        <p className="drawer-explanation">La persona recibirá un enlace único para activar su cuenta. El enlace vence según la política del espacio.</p>
        <Form form={form} layout="vertical" validateTrigger="onBlur" onValuesChange={() => setCreateDirty(true)} onFinish={(values) => void submit(values)}>
          <FormSection title="Datos de acceso" description="Definí la identidad, el correo y el rol inicial.">
            <Form.Item label="Usuario" name="username" rules={[{ required: true, message: "Indicá un usuario." }]}><Input autoComplete="off" /></Form.Item>
            <Form.Item label="Email" name="email" rules={[{ required: true, type: "email", message: "Indicá un email válido." }]}><Input type="email" autoComplete="email" /></Form.Item>
            <Form.Item label="Rol" name="role" initialValue="VENDEDOR" extra="El rol define qué puede consultar y modificar." rules={[{ required: true }]}><Select options={[{ value: "VENDEDOR", label: "Vendedor/a" }, { value: "ADMIN", label: "Administrador/a" }]} /></Form.Item>
          </FormSection>
        </Form>
        <StickySaveBar dirty={createDirty} feedback={saving ? { state: "saving", message: "Creando usuario…" } : { state: "idle", message: "Hay cambios sin guardar." }} onSave={() => void form.submit()} onCancel={closeCreate} />
      </Drawer>
      <Modal open={cancelOpen} title="Descartar usuario sin guardar" onCancel={() => setCancelOpen(false)} onOk={discardCreate} okText="Descartar cambios" cancelText="Seguir editando" okButtonProps={{ danger: true }}><p>Los datos ingresados se perderán si cerrás este panel.</p></Modal>
      <ConfirmDangerModal open={deactivateTarget !== null} title="Desactivar usuario" consequences={["La persona perderá el acceso al espacio de trabajo.", "No podrá iniciar sesión ni ejecutar acciones hasta que un administrador la active nuevamente.", "Sus registros y acciones auditadas se conservarán."]} confirmationWord="DESACTIVAR" dangerLabel="Desactivar usuario" confirming={roleSaving === deactivateTarget?.id} onCancel={() => setDeactivateTarget(null)} onConfirm={() => void confirmDeactivate()} />
    </Flex>
  );
}

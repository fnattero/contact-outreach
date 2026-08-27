"use client";

import { Alert, Button, Card, Flex, Form, Input, List, Select, Skeleton, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { AuthError, useAuth } from "@/components/auth-provider";
import {
  createUser,
  getUsers,
  problemMessage,
  updateUserRole,
  updateUserStatus,
  type CreatedUser,
  type ManagedUser,
  type Problem,
} from "@/lib/api";

type UserForm = {
  username: string;
  email: string;
  role: ManagedUser["role"];
};

export default function UsersSettingsPage() {
  const { session } = useAuth();
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [created, setCreated] = useState<CreatedUser | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  function refresh() {
    void getUsers()
      .then(setUsers)
      .catch(setError)
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    let cancelled = false;
    void getUsers()
      .then((nextUsers) => {
        if (!cancelled) setUsers(nextUsers);
      })
      .catch((problem) => {
        if (!cancelled) setError(problem);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (session?.role !== "ADMIN") {
    return <AuthError error={{ detail: "No tenés permisos para administrar usuarios." }} />;
  }

  async function submit(values: UserForm) {
    setSaving(true);
    setError(null);
    setCreated(null);
    try {
      setCreated(await createUser(values));
      refresh();
    } catch (problem) {
      setError(problem);
    } finally {
      setSaving(false);
    }
  }

  async function changeRole(user: ManagedUser, role: ManagedUser["role"]) {
    setError(null);
    try {
      const updated = await updateUserRole(user.id, role);
      setUsers((current) => current.map((item) => (item.id === updated.id ? updated : item)));
    } catch (problem) {
      setError(problem);
    }
  }

  async function changeStatus(user: ManagedUser) {
    setError(null);
    try {
      const updated = await updateUserStatus(user.id, !user.is_active);
      setUsers((current) => current.map((item) => (item.id === updated.id ? updated : item)));
    } catch (problem) {
      setError(problem);
    }
  }

  if (loading) return <Skeleton active paragraph={{ rows: 8 }} />;
  return (
    <Flex vertical gap="large">
      <div>
        <Typography.Title level={2}>Usuarios</Typography.Title>
        <Typography.Paragraph type="secondary">
          El alta se completa mediante un enlace único de activación con vencimiento.
        </Typography.Paragraph>
      </div>
      {error ? <Alert type="error" showIcon message={problemMessage(error as Problem)} /> : null}
      {created ? (
        <Alert
          type="success"
          showIcon
          message="Usuario creado. Copiá el enlace de activación ahora."
          description={
            <Flex vertical gap="small">
              <Typography.Text type="secondary">Vence: {new Date(created.expires_at).toLocaleString("es-AR")}</Typography.Text>
              <Input readOnly value={created.activation_url} aria-label="Enlace de activación" />
            </Flex>
          }
        />
      ) : null}
      <Card title="Crear usuario">
        <Form layout="vertical" onFinish={(values) => void submit(values as UserForm)}>
          <Form.Item label="Usuario" name="username" rules={[{ required: true }]}> <Input autoComplete="off" /> </Form.Item>
          <Form.Item label="Email" name="email" rules={[{ required: true, type: "email" }]}> <Input type="email" autoComplete="email" /> </Form.Item>
          <Form.Item label="Rol" name="role" initialValue="VENDEDOR" rules={[{ required: true }]}> 
            <Select options={[{ value: "VENDEDOR", label: "Vendedor/a" }, { value: "ADMIN", label: "Administrador/a" }]} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={saving}>Crear usuario</Button>
        </Form>
      </Card>
      <Card title="Usuarios del espacio de trabajo">
        <List
          dataSource={users}
          renderItem={(user) => (
            <List.Item
              actions={[
                <Select
                  key="role"
                  value={user.role}
                  aria-label={`Rol de ${user.username}`}
                  onChange={(role: ManagedUser["role"]) => void changeRole(user, role)}
                  options={[{ value: "VENDEDOR", label: "Vendedor/a" }, { value: "ADMIN", label: "Administrador/a" }]}
                  style={{ minWidth: 140 }}
                />,
                <Button key="status" onClick={() => void changeStatus(user)} disabled={user.id === session.id}>
                  {user.is_active ? "Desactivar" : "Activar"}
                </Button>,
              ]}
            >
              <List.Item.Meta title={user.username} description={user.email || "Sin email"} />
              <Tag color={user.is_active ? "green" : "default"}>{user.is_active ? "Activo" : "Inactivo"}</Tag>
            </List.Item>
          )}
        />
      </Card>
    </Flex>
  );
}

"use client";

import { Alert, Button, Card, Flex, Layout, Menu, Spin, Typography } from "antd";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { getSession, logout, problemMessage, type Problem, type UserSession } from "@/lib/api";

type AuthContextValue = {
  session: UserSession | null;
  loading: boolean;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth debe usarse dentro de AuthProvider");
  return value;
}

function errorText(error: unknown): string {
  return problemMessage((error as Problem) ?? {});
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [session, setSession] = useState<UserSession | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const nextSession = await getSession();
      setSession(nextSession);
    } catch {
      setSession(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void getSession()
      .then((nextSession) => {
        if (!cancelled) setSession(nextSession);
      })
      .catch(() => {
        if (!cancelled) setSession(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const signOut = useCallback(async () => {
    try {
      await logout();
    } finally {
      setSession(null);
      router.replace("/login");
    }
  }, [router]);

  const value = useMemo(() => ({ session, loading, refresh, signOut }), [loading, refresh, session, signOut]);

  if (pathname === "/login" || pathname === "/activate") {
    return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
  }

  if (loading) {
    return (
      <main className="centered-page" aria-label="Cargando aplicación">
        <Spin size="large" />
      </main>
    );
  }

  if (!session) {
    return (
      <main className="centered-page">
        <Card className="auth-card">
          <Flex vertical gap="middle">
            <Alert type="warning" message="La sesión expiró" description="Volvé a iniciar sesión para continuar." />
            <Button type="primary" onClick={() => router.replace("/login")}>
              Ir a iniciar sesión
            </Button>
          </Flex>
        </Card>
      </main>
    );
  }

  return (
    <AuthContext.Provider value={value}>
      <Layout className="app-layout">
        <Layout.Sider breakpoint="lg" collapsedWidth={0} theme="light">
          <div className="app-brand">Contact Outreach</div>
          <Menu
            mode="inline"
            selectedKeys={[pathname]}
            items={[
              { key: "/dashboard", label: <Link href="/dashboard">Resumen</Link> },
              { key: "/contacts", label: <Link href="/contacts">Contactos</Link> },
              { key: "/campaigns", label: <Link href="/campaigns">Campañas</Link> },
              ...(session.role === "ADMIN"
                ? [{ key: "/settings/users", label: <Link href="/settings/users">Usuarios</Link> }]
                : []),
            ]}
          />
        </Layout.Sider>
        <Layout>
          <Layout.Header className="app-header">
            <Flex justify="space-between" align="center" wrap>
              <Typography.Text strong>{session.workspace_name}</Typography.Text>
              <Flex align="center" gap="small">
                <Typography.Text>{session.username}</Typography.Text>
                <Button type="text" onClick={() => void signOut()}>
                  Cerrar sesión
                </Button>
              </Flex>
            </Flex>
          </Layout.Header>
          <Layout.Content className="app-content">{children}</Layout.Content>
        </Layout>
      </Layout>
    </AuthContext.Provider>
  );
}

export function AuthError({ error }: { error: unknown }) {
  return <Alert type="error" showIcon message={errorText(error)} />;
}

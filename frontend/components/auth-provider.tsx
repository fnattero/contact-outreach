"use client";

import { Alert } from "antd";
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
import { AppShell } from "@/components/app-shell";
import { ErrorState, LoadingState } from "@/components/design-system/states";
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
      <main className="shell-loading-page">
        <LoadingState layout="shell" label="Cargando aplicación" />
      </main>
    );
  }

  if (!session) {
    return (
      <main className="centered-page">
        <ErrorState
          failed="La sesión expiró"
          instruction="Iniciá sesión de nuevo para continuar trabajando."
          retryLabel="Ir a iniciar sesión"
          onRetry={() => router.replace("/login")}
        />
      </main>
    );
  }

  return (
    <AuthContext.Provider value={value}>
      <AppShell session={session} onSignOut={signOut}>{children}</AppShell>
    </AuthContext.Provider>
  );
}

export function AuthError({ error }: { error: unknown }) {
  return <Alert type="error" showIcon message={errorText(error)} />;
}

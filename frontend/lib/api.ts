export type Problem = {
  type?: string;
  title?: string;
  status?: number;
  code?: string;
  detail?: string;
  correlation_id?: string;
  field_errors?: Record<string, string | string[]>;
};

export type UserSession = {
  id: number;
  username: string;
  email: string;
  role: "ADMIN" | "VENDEDOR";
  workspace_id: string;
  workspace_name: string;
  capabilities: string[];
  session_expires_at: string;
  reauthentication_active: boolean;
};

type ApiEnvelope<T> = { data: T };

let csrfToken: string | null = null;

function correlationId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function responseError(response: Response): Promise<Problem> {
  try {
    return (await response.json()) as Problem;
  } catch {
    return {
      status: response.status,
      code: "invalid_response",
      detail: "No fue posible interpretar la respuesta del servidor.",
    };
  }
}

export async function getCsrfToken(): Promise<string> {
  const response = await fetch("/api/v1/auth/csrf/", {
    credentials: "include",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw await responseError(response);
  }
  const body = (await response.json()) as ApiEnvelope<{ csrf_token: string }>;
  csrfToken = body.data.csrf_token;
  return csrfToken;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  headers.set("X-Correlation-ID", correlationId());
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-CSRFToken", csrfToken ?? (await getCsrfToken()));
  }
  const response = await fetch(path, {
    ...init,
    method,
    headers,
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) {
    const problem = await responseError(response);
    if (response.status === 401) {
      csrfToken = null;
    }
    throw problem;
  }
  if (response.status === 204) {
    return undefined as T;
  }
  const body = (await response.json()) as ApiEnvelope<T>;
  return body.data;
}

export function getSession(): Promise<UserSession> {
  return request<UserSession>("/api/v1/auth/session/");
}

export function login(username: string, password: string): Promise<UserSession> {
  return request<UserSession>("/api/v1/auth/login/", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function activate(
  token: string,
  password: string,
  passwordConfirmation: string,
): Promise<UserSession> {
  return request<UserSession>("/api/v1/auth/activate/", {
    method: "POST",
    body: JSON.stringify({
      token,
      password,
      password_confirmation: passwordConfirmation,
    }),
  });
}

export function logout(): Promise<void> {
  return request<void>("/api/v1/auth/logout/", { method: "POST", body: "{}" });
}

export function problemMessage(problem: Problem): string {
  return problem.detail ?? "No fue posible completar la solicitud.";
}

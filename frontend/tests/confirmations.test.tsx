import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { App } from "antd";
import { createElement, type ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ResponseThreadPage from "@/app/responses/[id]/page";
import IntegrationsSettingsPage from "@/app/settings/integrations/page";
import JobsPage from "@/app/jobs/page";
import {
  disconnectGmail,
  getBackgroundJobs,
  getGmailConnection,
  getInboundThread,
  getIntegrationConfiguration,
  getIntegrationStatus,
  retryBackgroundJob,
  sendManualReply,
} from "@/lib/api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useParams: () => ({ id: "message-1" }),
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/components/auth-provider", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/auth-provider")>();
  return {
    ...actual,
    useAuth: () => ({
      session: { role: "ADMIN", capabilities: ["send_replies"] },
      loading: false,
      refresh: vi.fn(),
      signOut: vi.fn(),
    }),
  };
});

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    disconnectGmail: vi.fn(),
    getBackgroundJobs: vi.fn(),
    getGmailConnection: vi.fn(),
    getInboundThread: vi.fn(),
    getIntegrationConfiguration: vi.fn().mockReturnValue(new Promise(() => undefined)),
    getIntegrationStatus: vi.fn(),
    retryBackgroundJob: vi.fn(),
    sendManualReply: vi.fn(),
  };
});

function inApp(element: ReactElement) {
  return render(createElement(App, null, element));
}

async function confirmInDialog(buttonName: string) {
  const dialog = await screen.findByRole("dialog");
  const confirm = within(dialog).getByRole("button", { name: buttonName });
  expect(confirm).toBeDisabled();
  fireEvent.change(within(dialog).getByLabelText(/Escribí CONFIRMAR/), { target: { value: "CONFIRMAR" } });
  await waitFor(() => expect(within(dialog).getByRole("button", { name: buttonName })).toBeEnabled());
  fireEvent.click(within(dialog).getByRole("button", { name: buttonName }));
}

describe("gmail disconnect", () => {
  beforeEach(() => {
    vi.mocked(disconnectGmail).mockReset().mockResolvedValue({ connected: false, email: null, status: "DISCONNECTED" } as never);
    vi.mocked(getGmailConnection).mockResolvedValue({ connected: true, email: "ventas@empresa.example", status: "CONNECTED" } as never);
    vi.mocked(getIntegrationStatus).mockResolvedValue({
      extractor: { provider: "fake", overture_min_confidence: "0.750" },
      website_fetcher: { provider: "fake" },
      llm: { provider: "fake", model: "fake", credential_source: "NONE", configured: false },
      embeddings: { provider: "fake", model: "fake", dimensions: 1536 },
      gmail: { provider: "fake", oauth_client_id_configured: false, credential_source: "NONE", credential_configured: false, connection_status: "CONNECTED", email: "ventas@empresa.example" },
      revision: 1,
    } as never);
    vi.mocked(getIntegrationConfiguration).mockReturnValue(new Promise(() => undefined));
  });

  it("disconnects only after the confirmation word is typed", async () => {
    inApp(createElement(IntegrationsSettingsPage));
    fireEvent.click(await screen.findByRole("button", { name: "Acciones de Gmail" }));
    fireEvent.click(await screen.findByText("Desconectar"));

    expect(disconnectGmail).not.toHaveBeenCalled();
    await confirmInDialog("Desconectar Gmail");
    await waitFor(() => expect(disconnectGmail).toHaveBeenCalledTimes(1));
  });
});

describe("manual reply", () => {
  beforeEach(() => {
    vi.mocked(sendManualReply).mockReset().mockResolvedValue({ created: true } as never);
    vi.mocked(getInboundThread).mockReset().mockResolvedValue({
      inbound: { id: "message-1", subject: "Consulta", classification_label: "Otro", is_human: true },
      timeline: [],
    } as never);
  });

  it("sends only after the confirmation word is typed", async () => {
    inApp(createElement(ResponseThreadPage));
    fireEvent.change(await screen.findByLabelText("Texto de la respuesta"), { target: { value: "Gracias, le enviamos el catálogo." } });
    fireEvent.click(screen.getByRole("button", { name: "Revisar y autorizar" }));

    expect(sendManualReply).not.toHaveBeenCalled();
    await confirmInDialog("Autorizar y enviar");
    await waitFor(() => expect(sendManualReply).toHaveBeenCalledTimes(1));
    expect(vi.mocked(sendManualReply).mock.calls[0].slice(0, 2)).toEqual(["message-1", "Gracias, le enviamos el catálogo."]);
  });
});

describe("failed job retry", () => {
  beforeEach(() => {
    vi.mocked(retryBackgroundJob).mockReset().mockResolvedValue({} as never);
    vi.mocked(getBackgroundJobs).mockReset().mockResolvedValue({
      data: [{
        id: "job-1", created_at: "2026-10-01T10:00:00Z", task_name: "mailbox.deliver_message", entity_type: "OutboundMessage",
        entity_id: "m-1", queue: "default", state: "FAILED", state_label: "Falló", attempts: 3, heartbeat_at: null,
        started_at: "2026-10-01T10:00:01Z", finished_at: "2026-10-01T10:00:09Z", next_retry_at: null, error: "timeout",
      }],
      meta: { page: 1, page_size: 25, total: 1 },
    } as never);
  });

  it("retries the send only after the confirmation word is typed", async () => {
    const { container } = inApp(createElement(JobsPage));
    await screen.findByText("Entregar correo");
    fireEvent.click(container.querySelector(".ant-table-row-expand-icon") as Element);
    fireEvent.change(await screen.findByLabelText("Motivo de reintento job-1"), { target: { value: "Se restauró la conexión con Gmail" } });
    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));

    expect(retryBackgroundJob).not.toHaveBeenCalled();
    await confirmInDialog("Reintentar envío");
    await waitFor(() => expect(retryBackgroundJob).toHaveBeenCalledWith("job-1", "Se restauró la conexión con Gmail"));
  });
});

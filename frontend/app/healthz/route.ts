export const dynamic = "force-dynamic";

// Liveness for the platform health check; it reports nothing about the environment.
export function GET(): Response {
  return Response.json({ status: "ok" }, { headers: { "Cache-Control": "private, no-store" } });
}

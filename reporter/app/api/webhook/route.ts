// Anthropic Managed Agents webhook receiver — the serverless session driver.
//
// Wakes on session state transitions. On `session.status_idled` (the agent is
// waiting on a custom-tool result), service the pending tool calls and send the
// compact results back. HMAC-verified via the SDK; raw body is required.

import { getClient } from "@/lib/anthropic";
import { serviceSession } from "@/lib/sessionDriver";

/* eslint-disable @typescript-eslint/no-explicit-any */

export const runtime = "nodejs";
export const maxDuration = 300; // tool servicing (scrape/route/RAG) can take a while

export async function POST(req: Request): Promise<Response> {
  const raw = await req.text();
  const headers = Object.fromEntries(req.headers.entries());
  const client = getClient();

  let event: any;
  try {
    event = await (client as any).beta.webhooks.unwrap(raw, { headers });
  } catch {
    return new Response("invalid signature", { status: 400 });
  }

  const type: string = event?.data?.type;
  const sessionId: string = event?.data?.id;

  // We only need to act when the agent is blocked on us.
  if (type === "session.status_idled" && sessionId) {
    try {
      const { serviced, escalations } = await serviceSession(client, sessionId);
      return Response.json({ ok: true, serviced, escalations });
    } catch (e: any) {
      console.error("serviceSession failed", { sessionId, error: e?.message });
      return Response.json({ ok: false, error: e?.message }, { status: 500 });
    }
  }

  // Acknowledge everything else (run_started, terminated, etc.).
  return Response.json({ ok: true, ignored: type });
}

// Webhook-driven session driver.
//
// On `session.status_idled` (requires_action), service every pending custom-tool
// call: list events, find `agent.custom_tool_use` events without a matching
// `user.custom_tool_result`, dispatch each host-side, and send the compact
// result back. No long-lived process — the key serverless enabler.

import type Anthropic from "@anthropic-ai/sdk";
import { dispatchTool } from "./customTools";
import type { PendingToolCall } from "./types";

/* eslint-disable @typescript-eslint/no-explicit-any */

export async function findPendingToolCalls(client: Anthropic, sessionId: string): Promise<PendingToolCall[]> {
  const pending = new Map<string, PendingToolCall>();
  const answered = new Set<string>();

  // Auto-paginates across all pages.
  for await (const ev of (client as any).beta.sessions.events.list(sessionId)) {
    if (ev.type === "agent.custom_tool_use") {
      pending.set(ev.id, { eventId: ev.id, name: ev.name, input: ev.input ?? {} });
    } else if (ev.type === "user.custom_tool_result") {
      answered.add(ev.custom_tool_use_id);
    }
  }

  for (const id of answered) pending.delete(id);
  return [...pending.values()];
}

export async function serviceSession(client: Anthropic, sessionId: string): Promise<{ serviced: number; escalations: string[] }> {
  const calls = await findPendingToolCalls(client, sessionId);
  const escalations: string[] = [];

  for (const call of calls) {
    const result = await dispatchTool(call.name, call.input);

    await (client as any).beta.sessions.events.send(sessionId, {
      events: [
        {
          type: "user.custom_tool_result",
          custom_tool_use_id: call.eventId,
          content: [{ type: "text", text: result.content }],
          is_error: result.isError,
        },
      ],
    });

    if (result.escalate) {
      escalations.push(result.escalateReason ?? call.name);
      await notifyHumanQueue(sessionId, result.escalateReason ?? call.name);
    }
  }

  return { serviced: calls.length, escalations };
}

// Routes a run to human review (autonomous mode). The headline recommendation is
// always human-decided, so escalation = "a person must look now", not "stop".
// TODO: wire to the real notification channel (email/Slack via the website).
async function notifyHumanQueue(sessionId: string, reason: string): Promise<void> {
  const url = process.env.HUMAN_QUEUE_WEBHOOK_URL;
  if (!url) {
    console.warn(`[escalation] session=${sessionId} reason=${reason} (no HUMAN_QUEUE_WEBHOOK_URL configured)`);
    return;
  }
  await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ sessionId, reason, at: new Date().toISOString() }),
  }).catch((e) => console.error("human-queue notify failed", e));
}

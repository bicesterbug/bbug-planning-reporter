// Host-side custom-tool dispatch.
//
// The Managed Agent emits `agent.custom_tool_use`; the webhook driver calls
// dispatchTool() here, which routes to the correct internal endpoint (Python
// Vercel functions for the deterministic engines, TS routes for RAG/Haiku) and
// returns COMPACT JSON. The agent container never executes these and never sees
// raw documents/HTML/OSM — only the compact result.

import { requireEnv } from "./anthropic";
import type { ToolResult } from "./types";

type Route = { path: string };

// tool name → internal endpoint. Python functions live at top-level /api/tools/*;
// TS routes live under /app/api/* (Next.js route handlers).
const ROUTES: Record<string, Route> = {
  fetch_application: { path: "/api/tools/fetch_application" }, // python
  get_site_boundary: { path: "/api/tools/site_boundary" }, // python
  assess_cycle_route: { path: "/api/tools/assess_route" }, // python
  ingest_document: { path: "/api/tools/ingest" }, // ts (orchestrates extract + OCR + embed + store)
  search_application_docs: { path: "/api/tools/search" }, // ts
  search_policy: { path: "/api/tools/search" }, // ts (collection in input)
  classify_documents: { path: "/api/classify" }, // ts (task in input)
  generate_search_queries: { path: "/api/classify" }, // ts
  verify_claims: { path: "/api/classify" }, // ts
};

// Tools intentionally not yet wired.
//  - download_docs: ingest_document fetches by URL directly; Blob archival is a
//    separate follow-up.
//  - get_policy_section / export_response: pending.
const NOT_IMPLEMENTED = new Set(["download_docs", "get_policy_section", "export_response"]);

function baseUrl(): string {
  // REPORTER_BASE_URL e.g. https://reporter.bicesterbug.org ; on Vercel can be
  // derived from VERCEL_URL.
  const explicit = process.env.REPORTER_BASE_URL;
  if (explicit) return explicit.replace(/\/$/, "");
  if (process.env.VERCEL_URL) return `https://${process.env.VERCEL_URL}`;
  return "http://localhost:3000";
}

export async function dispatchTool(name: string, input: Record<string, unknown>): Promise<ToolResult> {
  // High-risk gated tool: record the inference; autonomous mode routes it to a
  // human queue. (Storage wiring is a follow-up; we ack so the loop proceeds.)
  if (name === "submit_mitigation_inference") {
    return {
      content: JSON.stringify({ recorded: true, requires_human_review: true }),
      isError: false,
      escalate: true,
      escalateReason: "mitigation_inference_checkpoint",
    };
  }

  if (NOT_IMPLEMENTED.has(name)) {
    return {
      content: JSON.stringify({ error: `tool '${name}' not yet implemented in serverless build` }),
      isError: true,
    };
  }

  const route = ROUTES[name];
  if (!route) {
    return { content: JSON.stringify({ error: `unknown tool '${name}'` }), isError: true };
  }

  // search/classify carry the discriminator (collection/task) so a single
  // endpoint can serve several logical tools.
  const payload = decorate(name, input);

  const resp = await fetch(`${baseUrl()}${route.path}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-internal-token": requireEnv("INTERNAL_TOOL_TOKEN"),
    },
    body: JSON.stringify(payload),
  });

  const text = await resp.text();
  if (!resp.ok) {
    return { content: JSON.stringify({ error: `tool '${name}' failed`, status: resp.status, body: text }), isError: true };
  }

  // Surface a deterministic escalation flag if the tool returned one.
  let escalate = false;
  let escalateReason: string | undefined;
  try {
    const parsed = JSON.parse(text);
    if (parsed?.escalate) {
      escalate = true;
      escalateReason = parsed.escalate_reason;
    }
  } catch {
    /* tool returned non-JSON; pass through as-is */
  }

  return { content: text, isError: false, escalate, escalateReason };
}

function decorate(name: string, input: Record<string, unknown>): Record<string, unknown> {
  switch (name) {
    case "search_application_docs":
      return { ...input, collection: "application" };
    case "search_policy":
      return { ...input, collection: "policy" };
    case "classify_documents":
      return { ...input, task: "filter" };
    case "generate_search_queries":
      return { ...input, task: "queries" };
    case "verify_claims":
      return { ...input, task: "verify" };
    default:
      return input;
  }
}

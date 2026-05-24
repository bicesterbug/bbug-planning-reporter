// Host-side cheap classification (Haiku 4.5) — kept OFF the premium agent loop
// to preserve the cost split. Serves three logical tools via `task`:
//   - filter:  select transport-relevant documents to ingest
//   - queries: generate application + policy search queries
//   - verify:  check the draft's claims against the evidence
//
// These mirror the discrete Haiku calls in the legacy AgentOrchestrator; the
// prompt text can be refined from src/agent/prompts/* as raw material.

import Anthropic from "@anthropic-ai/sdk";
import { CLASSIFIER_MODEL, requireEnv } from "@/lib/anthropic";

export const runtime = "nodejs";

const SYSTEMS: Record<string, string> = {
  filter:
    "You select which planning-application documents are relevant to a transport/cycling assessment. "
    + "Exclude consultation responses and public comments unless explicitly told to include them. "
    + "Reply ONLY with a JSON object: {\"selected_ids\": [string]}.",
  queries:
    "You generate search queries for a transport/cycling planning assessment. Given application metadata "
    + "and the ingested document list, produce focused queries. Reply ONLY with JSON: "
    + "{\"application_queries\": [string], \"policy_queries\": [string]}.",
  verify:
    "You verify whether each substantive claim in a draft is supported by the supplied evidence chunks. "
    + "Reply ONLY with JSON: {\"claims\": [{\"claim\": string, \"verified\": boolean, \"evidence\": string}]}.",
};

export async function POST(req: Request): Promise<Response> {
  if (process.env.INTERNAL_TOOL_TOKEN && req.headers.get("x-internal-token") !== process.env.INTERNAL_TOOL_TOKEN) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  const body = await req.json().catch(() => ({}));
  const task: string = body.task;
  const system = SYSTEMS[task];
  if (!system) return Response.json({ error: `unknown task '${task}'` }, { status: 400 });

  const client = new Anthropic({ apiKey: requireEnv("ANTHROPIC_API_KEY") });
  const { task: _omit, ...payload } = body;

  const msg = await client.messages.create({
    model: CLASSIFIER_MODEL,
    max_tokens: 2048,
    system,
    messages: [{ role: "user", content: JSON.stringify(payload) }],
  });

  const text = msg.content.find((b) => b.type === "text")?.type === "text"
    ? (msg.content.find((b) => b.type === "text") as { text: string }).text
    : "{}";

  try {
    return Response.json(JSON.parse(text.trim()));
  } catch {
    return Response.json({ error: "classifier returned non-JSON", raw: text }, { status: 502 });
  }
}

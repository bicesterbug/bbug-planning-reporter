// Trigger endpoint — creates a session for an application (both modes from the
// one shared agent config).
//
//   POST { application_ref, mode: "autonomous" | "cowork" }
//
// Autonomous: kick off with `user.define_outcome` whose rubric requires the full
// evidence base + asks + citations but EXCLUDES the headline recommendation.
// Cowork: open the session and send a first message; the human drives checkpoints.
//
// The agent + environment are created once out-of-band via the `ant` CLI
// (agent/agent.yaml, agent/environment.yaml); this only references their IDs.

import { agentRef, getClient } from "@/lib/anthropic";
import type { ReviewMode } from "@/lib/types";

/* eslint-disable @typescript-eslint/no-explicit-any */

export const runtime = "nodejs";

const OUTCOME_RUBRIC = `
Produce a draft cycling-advocacy consultation response for the application. It MUST contain:
1. Application context (site, scale, key transport characteristics) — every fact cited [Doc: …] or [Portal].
2. Baseline cycle accessibility to key destinations, using assess_cycle_route (current network only).
3. Assessment of the submission: what is present, what is MISSING, what is vague/aspirational.
4. Proposed mitigations inferred from the documents, each with: what is actually proposed (committed vs
   aspirational), affected route segments, whether it would change the baseline, a confidence score (0–1)
   with reasoning, and any credibility concerns. Submit each via submit_mitigation_inference.
5. The asks: planning conditions, S106 heads of terms, and pre-determination design amendments — each with
   policy basis, technical justification, indicative quantum/wording, and a fallback.
6. Inline citations on every substantive claim ([Doc: file p.N] / [Policy: ref §clause] / [Position: id]).

DO NOT state a headline position (support/object/conditional support) — that is decided by a human.
Write drafts to the workspace as 01-triage.md … 05-asks.md and the final response to
/mnt/session/outputs/advocacy-response.md.
`.trim();

export async function POST(req: Request): Promise<Response> {
  const body = await req.json().catch(() => ({}));
  const applicationRef: string | undefined = body.application_ref;
  const mode: ReviewMode = body.mode === "cowork" ? "cowork" : "autonomous";

  if (!applicationRef) {
    return Response.json({ error: "application_ref is required" }, { status: 400 });
  }

  const client = getClient();
  const { agentId, environmentId } = agentRef();

  const session = await (client as any).beta.sessions.create({
    agent: agentId,
    environment_id: environmentId,
    title: `${applicationRef} (${mode})`,
    metadata: { application_ref: applicationRef, mode },
  });

  if (mode === "autonomous") {
    await (client as any).beta.sessions.events.send(session.id, {
      events: [
        {
          type: "user.define_outcome",
          description: `Assess Cherwell planning application ${applicationRef} for cycling advocacy.`,
          rubric: { type: "text", content: OUTCOME_RUBRIC },
          max_iterations: 5,
        },
      ],
    });
  } else {
    await (client as any).beta.sessions.events.send(session.id, {
      events: [
        {
          type: "user.message",
          content: [
            {
              type: "text",
              text: `Begin a cowork assessment of ${applicationRef}. Work to the first checkpoint only `
                + `(acquire + triage): fetch the application, ingest documents, and produce 01-triage.md `
                + `with the document manifest, gap flags, and vague-commitment flags. Then stop and summarise `
                + `for my review before continuing.`,
            },
          ],
        },
      ],
    });
  }

  const consoleUrl = `https://platform.claude.com/workspaces/default/sessions/${session.id}`;
  return Response.json({ session_id: session.id, mode, application_ref: applicationRef, console_url: consoleUrl });
}

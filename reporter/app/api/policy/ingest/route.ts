// Admin: policy KB ingest (NOT an agent tool).
//
// Populates policy_chunks for a policy source revision. Two input shapes:
//   - structured: sections: [{ section_ref, text, binding? }]
//   - prose:      document_url (fetched + extracted via the pymupdf function),
//                 chunked with section_ref = "p.N"
//
// Revisioning: by default the new revision supersedes the prior open one
// (half-open intervals). Re-runnable for the same revision (it re-opens nothing;
// duplicates are possible, so prefer deleting a revision before re-ingesting it).
//
// Auth: INTERNAL_TOOL_TOKEN (this is a seed/ops endpoint, not exposed to the agent).

import { requireEnv } from "@/lib/anthropic";
import { splitText } from "@/lib/chunk";
import { closeOpenRevisions, insertPolicyChunks, type PolicyChunk } from "@/lib/policy";
import { embed } from "@/lib/voyage";

export const runtime = "nodejs";
export const maxDuration = 300;

interface SectionInput {
  section_ref: string;
  text: string;
  binding?: boolean;
}

function internalBase(): string {
  if (process.env.REPORTER_BASE_URL) return process.env.REPORTER_BASE_URL.replace(/\/$/, "");
  if (process.env.VERCEL_URL) return `https://${process.env.VERCEL_URL}`;
  return "http://localhost:3000";
}

export async function POST(req: Request): Promise<Response> {
  if (process.env.INTERNAL_TOOL_TOKEN && req.headers.get("x-internal-token") !== process.env.INTERNAL_TOOL_TOKEN) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  const body = await req.json().catch(() => ({}));
  const source: string = body.source;
  const revisionId: string = body.revision_id;
  const effectiveFrom: string = body.effective_from;
  const effectiveTo: string | null = body.effective_to ?? null;
  const defaultBinding: boolean = body.binding ?? true;
  const supersede: boolean = body.supersede_previous ?? true;

  if (!source || !revisionId || !effectiveFrom) {
    return Response.json({ error: "source, revision_id and effective_from are required" }, { status: 400 });
  }

  // Build (section_ref, binding, text-part) units.
  const units: { sectionRef: string; binding: boolean; text: string }[] = [];

  if (Array.isArray(body.sections)) {
    for (const s of body.sections as SectionInput[]) {
      for (const part of splitText(s.text)) {
        units.push({ sectionRef: s.section_ref, binding: s.binding ?? defaultBinding, text: part });
      }
    }
  } else if (body.document_url) {
    const resp = await fetch(`${internalBase()}/api/tools/extract_document`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-internal-token": requireEnv("INTERNAL_TOOL_TOKEN") },
      body: JSON.stringify({ document_url: body.document_url, render_images: false }),
    });
    const extract = await resp.json();
    if (!resp.ok || extract.error) {
      return Response.json({ error: "extraction failed", detail: extract.error }, { status: 502 });
    }
    for (const p of extract.pages as { page: number; text: string }[]) {
      for (const part of splitText((p.text || "").trim())) {
        if (part.length >= 20) units.push({ sectionRef: `p.${p.page}`, binding: defaultBinding, text: part });
      }
    }
  } else {
    return Response.json({ error: "provide either sections[] or document_url" }, { status: 400 });
  }

  if (units.length === 0) return Response.json({ error: "no ingestable text" }, { status: 400 });

  const vectors = await embed(units.map((u) => u.text), "document");

  if (supersede) await closeOpenRevisions(source, revisionId, effectiveFrom);

  const chunks: PolicyChunk[] = units.map((u, i) => ({
    source,
    sectionRef: u.sectionRef,
    binding: u.binding,
    text: u.text,
    embedding: vectors[i],
    revisionId,
    effectiveFrom,
    effectiveTo,
  }));
  const indexed = await insertPolicyChunks(chunks);

  const sections = new Set(units.map((u) => u.sectionRef));
  return Response.json({ source, revision_id: revisionId, sections_ingested: sections.size, chunks_indexed: indexed });
}

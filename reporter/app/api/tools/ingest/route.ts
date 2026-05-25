// Custom tool: ingest_document.
//
// One document → indexed RAG chunks. Born-digital text comes from the Python
// extractor (pymupdf); scanned pages are transcribed by Claude vision (the
// Claude-native OCR that replaces Tesseract). Chunks are embedded with Voyage and
// stored in Neon. Returns a COMPACT manifest only — the document text never
// reaches the agent; it queries it later via search_application_docs.

import Anthropic from "@anthropic-ai/sdk";
import { AGENT_LOOP_MODEL, requireEnv } from "@/lib/anthropic";
import { chunkPages } from "@/lib/chunk";
import { deleteAppDoc, insertAppChunks } from "@/lib/db";
import { embed } from "@/lib/voyage";

export const runtime = "nodejs";
export const maxDuration = 300;

interface ExtractedPage {
  page: number;
  char_count: number;
  is_image_based: boolean;
  text: string;
}

function internalBase(): string {
  if (process.env.REPORTER_BASE_URL) return process.env.REPORTER_BASE_URL.replace(/\/$/, "");
  if (process.env.VERCEL_URL) return `https://${process.env.VERCEL_URL}`;
  return "http://localhost:3000";
}

const DOC_TYPE_RULES: [RegExp, string][] = [
  [/transport assessment|\bta\b/i, "transport_assessment"],
  [/transport statement|\bts\b/i, "transport_statement"],
  [/travel plan/i, "travel_plan"],
  [/design and access|\bdas\b/i, "design_and_access_statement"],
  [/planning statement/i, "planning_statement"],
  [/flood risk/i, "flood_risk_assessment"],
  [/\bplan\b|drawing|elevation|layout|site plan/i, "drawing"],
  [/officer report|delegated report/i, "officer_report"],
];

function classifyDoc(sourceFile: string, declaredType: string | null): string {
  if (declaredType) return declaredType;
  for (const [re, type] of DOC_TYPE_RULES) if (re.test(sourceFile)) return type;
  return "other";
}

async function transcribePage(client: Anthropic, b64: string): Promise<string> {
  const msg = await client.messages.create({
    model: AGENT_LOOP_MODEL,
    max_tokens: 4096,
    system: "Transcribe ALL text from this scanned planning-document page verbatim, preserving tables as best you can. Output only the transcribed text, nothing else.",
    messages: [
      {
        role: "user",
        content: [{ type: "image", source: { type: "base64", media_type: "image/png", data: b64 } }],
      },
    ],
  });
  const block = msg.content.find((b) => b.type === "text") as { text: string } | undefined;
  return block?.text ?? "";
}

export async function POST(req: Request): Promise<Response> {
  if (process.env.INTERNAL_TOOL_TOKEN && req.headers.get("x-internal-token") !== process.env.INTERNAL_TOOL_TOKEN) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  const body = await req.json().catch(() => ({}));
  const applicationRef: string = body.application_ref;
  const documentUrl: string = body.document_url;
  const sourceFile: string = body.source_file || documentUrl?.split("/").pop() || "document.pdf";
  const declaredType: string | null = body.document_type ?? null;
  if (!applicationRef || !documentUrl) {
    return Response.json({ error: "application_ref and document_url are required" }, { status: 400 });
  }

  // 1. Extract (Python pymupdf function).
  const extractResp = await fetch(`${internalBase()}/api/tools/extract_document`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-internal-token": requireEnv("INTERNAL_TOOL_TOKEN") },
    body: JSON.stringify({ document_url: documentUrl, render_images: true }),
  });
  const extract = await extractResp.json();
  if (!extractResp.ok || extract.error) {
    return Response.json({ error: "extraction failed", detail: extract.error ?? (await extractResp.text()) }, { status: 502 });
  }

  // 2. Claude-vision OCR for scanned pages.
  const client = new Anthropic({ apiKey: requireEnv("ANTHROPIC_API_KEY") });
  const pageImages: Record<string, string> = extract.page_images ?? {};
  let ocrPages = 0;
  let ocrFailed = false;
  const pages: { page: number; text: string }[] = [];

  for (const p of extract.pages as ExtractedPage[]) {
    let text = p.text ?? "";
    if (p.is_image_based) {
      const b64 = pageImages[String(p.page)];
      if (b64) {
        const transcribed = await transcribePage(client, b64);
        if (transcribed.trim().length > 0) {
          text = transcribed;
          ocrPages += 1;
        } else {
          ocrFailed = true; // a scanned page we couldn't read — hard-escalation trigger
        }
      } else {
        ocrFailed = true; // image page not rendered (over cap) — flag for review
      }
    }
    pages.push({ page: p.page, text });
  }

  // 3. Chunk → 4. Embed → 5. Store.
  const documentType = classifyDoc(sourceFile, declaredType);
  const chunks = chunkPages(pages);
  let chunksIndexed = 0;
  if (chunks.length > 0) {
    const vectors = await embed(chunks.map((c) => c.text), "document");
    await deleteAppDoc(applicationRef, sourceFile);
    chunksIndexed = await insertAppChunks(
      chunks.map((c, i) => ({
        applicationRef,
        sourceFile,
        pageNumber: c.page,
        documentType,
        text: c.text,
        embedding: vectors[i],
      })),
    );
  }

  // Compact manifest only — no document text returned to the agent.
  return Response.json({
    source_file: sourceFile,
    document_type: documentType,
    page_count: extract.page_count,
    extraction_method: extract.extraction_method,
    ocr_pages: ocrPages,
    chunks_indexed: chunksIndexed,
    image_pages: extract.image_pages,
    ...(ocrFailed ? { escalate: true, escalate_reason: "ocr_failed_or_redacted" } : {}),
  });
}

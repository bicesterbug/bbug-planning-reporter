// RAG search over Neon + pgvector — the token firewall.
//
// Serves search_application_docs (collection="application", filtered by
// application_ref) and search_policy (collection="policy", optional temporal
// filter). Embeds the query with Voyage, runs a cosine-similarity search, and
// returns only the top-N CITED chunks — raw documents never reach the agent.

import { getSql } from "@/lib/db";
import { embed } from "@/lib/voyage";

export const runtime = "nodejs";

export async function POST(req: Request): Promise<Response> {
  if (process.env.INTERNAL_TOOL_TOKEN && req.headers.get("x-internal-token") !== process.env.INTERNAL_TOOL_TOKEN) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  const body = await req.json().catch(() => ({}));
  const query: string = body.query;
  const collection: string = body.collection; // "application" | "policy"
  const nResults: number = Math.min(body.n_results ?? 10, 25);
  if (!query || !collection) {
    return Response.json({ error: "query and collection are required" }, { status: 400 });
  }

  const sql = getSql();
  const [embedding] = await embed([query], "query");
  const vec = `[${embedding.join(",")}]`;

  let rows: Record<string, unknown>[];
  if (collection === "application") {
    const applicationRef: string = body.application_ref;
    if (!applicationRef) return Response.json({ error: "application_ref required for application search" }, { status: 400 });
    rows = (await sql`
      SELECT chunk_text, source_file, page_number, document_type,
             1 - (embedding <=> ${vec}::vector) AS score
      FROM app_chunks
      WHERE application_ref = ${applicationRef}
      ORDER BY embedding <=> ${vec}::vector
      LIMIT ${nResults}
    `) as Record<string, unknown>[];
  } else {
    // Policy: optional temporal filter (clause effective on/before the date).
    const effectiveDate: string | null = body.effective_date ?? null;
    const sources: string[] | null = body.sources ?? null;
    rows = (await sql`
      SELECT chunk_text, source AS source_file, section_ref AS page_number, source AS document_type,
             1 - (embedding <=> ${vec}::vector) AS score
      FROM policy_chunks
      WHERE (${effectiveDate}::date IS NULL OR effective_from <= ${effectiveDate}::date)
        AND (${sources}::text[] IS NULL OR source = ANY(${sources}::text[]))
      ORDER BY embedding <=> ${vec}::vector
      LIMIT ${nResults}
    `) as Record<string, unknown>[];
  }

  const chunks = rows.map((r) => ({
    text: r.chunk_text,
    source: r.source_file,
    page: r.page_number,
    document_type: r.document_type,
    score: typeof r.score === "number" ? Number((r.score as number).toFixed(3)) : r.score,
    citation:
      collection === "application"
        ? `[Doc: ${r.source_file}${r.page_number ? ` p.${r.page_number}` : ""}]`
        : `[Policy: ${r.source_file}${r.page_number ? ` ${r.page_number}` : ""}]`,
  }));

  return Response.json({ collection, query, count: chunks.length, chunks });
}

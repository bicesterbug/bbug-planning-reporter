// Custom tool: get_policy_section.
//
// Exact retrieval of a named policy section (not vector search) — e.g. the full
// text of "Policy SLE 4" or LTN 1/20 "Table 5-2", optionally pinned to a revision
// or resolved for an application's effective date. Returns the section text with a
// citation and the binding-vs-aspirational flag.

import { getPolicySection } from "@/lib/policy";

export const runtime = "nodejs";

export async function POST(req: Request): Promise<Response> {
  if (process.env.INTERNAL_TOOL_TOKEN && req.headers.get("x-internal-token") !== process.env.INTERNAL_TOOL_TOKEN) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  const body = await req.json().catch(() => ({}));
  const source: string = body.source;
  const sectionRef: string = body.section_ref;
  if (!source || !sectionRef) {
    return Response.json({ error: "source and section_ref are required" }, { status: 400 });
  }

  const rows = await getPolicySection(source, sectionRef, {
    revisionId: body.revision_id,
    effectiveDate: body.effective_date,
  });

  if (rows.length === 0) {
    return Response.json({ error: "section_not_found", source, section_ref: sectionRef });
  }

  return Response.json({
    source,
    section_ref: rows[0].section_ref ?? sectionRef,
    binding: rows[0].binding,
    revision_id: rows[0].revision_id,
    text: rows.map((r) => r.chunk_text).join("\n\n"),
    citation: `[Policy: ${source} ${rows[0].section_ref ?? sectionRef}]`,
  });
}

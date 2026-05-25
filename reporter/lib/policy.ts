// Policy knowledge base helpers (Neon + pgvector).
//
// Temporal model: half-open intervals [effective_from, effective_to). A revision
// is in force for date D when effective_from <= D < coalesce(effective_to, ∞).
// Ingesting a new revision closes the prior open one (effective_to = new from),
// so exactly one revision matches any date — no overlap, no double-counting.
// This matches the legacy resolver's "newer wins on the boundary day".

import { getSql } from "./db";
import { toVector } from "./voyage";

export interface PolicyChunk {
  source: string; // e.g. LTN_1_20, NPPF, Cherwell_Local_Plan
  sectionRef: string | null; // e.g. "Policy SLE 4", "Table 5-2", "p.12"
  binding: boolean; // binding policy vs adopted strategy / best practice
  text: string;
  embedding: number[];
  revisionId: string | null;
  effectiveFrom: string; // ISO date
  effectiveTo: string | null; // ISO date or null (current)
}

// Close any still-open revision of this source so the new one supersedes it.
export async function closeOpenRevisions(source: string, newRevisionId: string, boundary: string): Promise<void> {
  const sql = getSql();
  await sql`
    UPDATE policy_chunks
    SET effective_to = ${boundary}::date
    WHERE source = ${source}
      AND effective_to IS NULL
      AND (revision_id IS DISTINCT FROM ${newRevisionId})
  `;
}

export async function insertPolicyChunks(chunks: PolicyChunk[]): Promise<number> {
  if (chunks.length === 0) return 0;
  const sql = getSql();
  let inserted = 0;
  for (const c of chunks) {
    await sql`
      INSERT INTO policy_chunks
        (source, section_ref, binding, chunk_text, embedding, revision_id, effective_from, effective_to)
      VALUES
        (${c.source}, ${c.sectionRef}, ${c.binding}, ${c.text}, ${toVector(c.embedding)}::vector,
         ${c.revisionId}, ${c.effectiveFrom}::date, ${c.effectiveTo ? c.effectiveTo : null}::date)
    `;
    inserted += 1;
  }
  return inserted;
}

export interface PolicySectionRow {
  chunk_text: string;
  section_ref: string | null;
  binding: boolean;
  revision_id: string | null;
}

// Exact section lookup (not vector search). Returns the chunks of one section in
// insertion order, optionally pinned to a revision or resolved for a date.
export async function getPolicySection(
  source: string,
  sectionRef: string,
  opts: { revisionId?: string; effectiveDate?: string } = {},
): Promise<PolicySectionRow[]> {
  const sql = getSql();
  const revisionId = opts.revisionId ?? null;
  const effectiveDate = opts.effectiveDate ?? null;
  return (await sql`
    SELECT chunk_text, section_ref, binding, revision_id
    FROM policy_chunks
    WHERE source = ${source}
      AND lower(section_ref) = lower(${sectionRef})
      AND (${revisionId}::text IS NULL OR revision_id = ${revisionId})
      AND (
        ${effectiveDate}::date IS NULL
        OR (effective_from <= ${effectiveDate}::date
            AND (effective_to IS NULL OR effective_to > ${effectiveDate}::date))
      )
    ORDER BY id
  `) as PolicySectionRow[];
}

export async function listPolicyRevisions(): Promise<Record<string, unknown>[]> {
  const sql = getSql();
  return (await sql`
    SELECT source, revision_id, min(effective_from) AS effective_from, max(effective_to) AS effective_to,
           count(*) AS chunks
    FROM policy_chunks
    GROUP BY source, revision_id
    ORDER BY source, effective_from
  `) as Record<string, unknown>[];
}

// Neon + pgvector access (replaces ChromaDB). HTTP serverless driver — one
// short-lived query per call, ideal for Vercel functions.

import { neon } from "@neondatabase/serverless";
import { requireEnv } from "./anthropic";
import { toVector } from "./voyage";

export function getSql() {
  return neon(requireEnv("DATABASE_URL"));
}

export interface AppChunk {
  applicationRef: string;
  sourceFile: string;
  pageNumber: number | null;
  documentType: string | null;
  text: string;
  embedding: number[];
}

// Insert application-document chunks. Embeddings are passed as pgvector literals.
export async function insertAppChunks(chunks: AppChunk[]): Promise<number> {
  if (chunks.length === 0) return 0;
  const sql = getSql();
  let inserted = 0;
  // Insert row-by-row (simple + safe with the HTTP driver). Volumes per document
  // are small (tens of chunks); batch via UNNEST later if it becomes a hotspot.
  for (const c of chunks) {
    await sql`
      INSERT INTO app_chunks (application_ref, source_file, page_number, document_type, chunk_text, embedding)
      VALUES (${c.applicationRef}, ${c.sourceFile}, ${c.pageNumber}, ${c.documentType}, ${c.text}, ${toVector(c.embedding)}::vector)
    `;
    inserted += 1;
  }
  return inserted;
}

// Idempotency: drop any prior chunks for this (application, file) before re-ingest.
export async function deleteAppDoc(applicationRef: string, sourceFile: string): Promise<void> {
  const sql = getSql();
  await sql`DELETE FROM app_chunks WHERE application_ref = ${applicationRef} AND source_file = ${sourceFile}`;
}

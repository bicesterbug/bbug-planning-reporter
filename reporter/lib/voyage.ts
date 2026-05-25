// Voyage AI embeddings (replaces the self-hosted sentence-transformers model).
// One place for the model + dimensions so search and ingest stay in sync.

import { requireEnv } from "./anthropic";

export const VOYAGE_MODEL = process.env.VOYAGE_MODEL || "voyage-3"; // 1024 dims

// Voyage caps batch size; chunk large inputs.
const MAX_BATCH = 128;

export async function embed(texts: string[], inputType: "query" | "document"): Promise<number[][]> {
  const out: number[][] = [];
  for (let i = 0; i < texts.length; i += MAX_BATCH) {
    const batch = texts.slice(i, i + MAX_BATCH);
    const resp = await fetch("https://api.voyageai.com/v1/embeddings", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${requireEnv("VOYAGE_API_KEY")}`,
      },
      body: JSON.stringify({ input: batch, model: VOYAGE_MODEL, input_type: inputType }),
    });
    if (!resp.ok) throw new Error(`voyage embed failed: ${resp.status} ${await resp.text()}`);
    const data = await resp.json();
    for (const row of data.data) out.push(row.embedding as number[]);
  }
  return out;
}

// pgvector literal for a single embedding.
export function toVector(embedding: number[]): string {
  return `[${embedding.join(",")}]`;
}

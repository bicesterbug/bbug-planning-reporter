// Simple page-aware text chunker (replaces langchain RecursiveCharacterTextSplitter).
// Chunks per page so every chunk keeps a precise page citation. Splits on
// paragraph/sentence boundaries, targeting ~1800 chars (~450 tokens) with overlap.

const MAX_CHARS = 1800;
const OVERLAP = 200;

export interface PageChunk {
  page: number;
  text: string;
}

export function chunkPages(pages: { page: number; text: string }[]): PageChunk[] {
  const chunks: PageChunk[] = [];
  for (const p of pages) {
    const clean = p.text.replace(/\s+\n/g, "\n").trim();
    if (clean.length < 20) continue;
    for (const part of splitText(clean)) {
      chunks.push({ page: p.page, text: part });
    }
  }
  return chunks;
}

export function splitText(text: string): string[] {
  if (text.length <= MAX_CHARS) return [text];

  // Prefer paragraph boundaries, then sentence, then hard cut.
  const units = text.split(/\n{2,}/);
  const out: string[] = [];
  let buf = "";

  const flush = () => {
    if (buf.trim()) out.push(buf.trim());
    buf = "";
  };

  for (const unit of units) {
    if (unit.length > MAX_CHARS) {
      flush();
      for (const sentence of unit.split(/(?<=[.!?])\s+/)) {
        if ((buf + " " + sentence).length > MAX_CHARS) {
          flush();
          if (sentence.length > MAX_CHARS) {
            for (let i = 0; i < sentence.length; i += MAX_CHARS - OVERLAP) {
              out.push(sentence.slice(i, i + MAX_CHARS));
            }
          } else {
            buf = sentence;
          }
        } else {
          buf = buf ? `${buf} ${sentence}` : sentence;
        }
      }
      continue;
    }
    if ((buf + "\n\n" + unit).length > MAX_CHARS) flush();
    buf = buf ? `${buf}\n\n${unit}` : unit;
  }
  flush();
  return out;
}

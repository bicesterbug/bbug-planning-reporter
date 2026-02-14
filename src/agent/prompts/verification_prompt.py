"""
Verification prompt builder for post-generation review validation.

Implements [review-workflow-redesign:FR-005] - Post-generation verification

Implements:
- [review-workflow-redesign:verification_prompt/TS-01] Prompt includes review and evidence
- [review-workflow-redesign:verification_prompt/TS-02] Specifies claim verification output
- [review-workflow-redesign:verification_prompt/TS-03] Includes ingested document list
"""

from typing import Any


def build_verification_prompt(
    review_markdown: str,
    review_structure: dict[str, Any],
    ingested_documents: list[dict[str, Any]],
    evidence_chunks: list[dict[str, Any]],
) -> tuple[str, str]:
    """
    Build the system and user prompts for post-generation review verification.

    The prompt asks Haiku to verify that claims, citations, and document
    references in the review are supported by the evidence and ingested
    document list.

    Args:
        review_markdown: The full markdown review text to verify.
        review_structure: The structured JSON assessment (dict form).
        ingested_documents: List of dicts with keys: description, document_type, url.
        evidence_chunks: List of evidence chunk dicts with keys: source, query, text, metadata.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    system_prompt = """You are a fact-checker for cycling advocacy reviews of planning applications.

Your task is to verify claims made in a review against the provided evidence and document list. Check that:

1. **Document references**: Any document mentioned in the review actually exists in the ingested document list.
2. **Factual claims**: Claims about the application (e.g. "provides 50 cycle parking spaces") are supported by evidence chunks.
3. **Policy citations**: Policy references (e.g. "NPPF para 115") appear in the policy evidence.

You MUST respond with a single JSON object and nothing else. No markdown, no commentary — only valid JSON.

The JSON object must have this schema:

{
  "claims": [
    {
      "claim": string,
      "verified": boolean,
      "source": string | null
    }
  ]
}

**claims**: An array of 5-15 key claims extracted from the review. For each claim:
- "claim": The specific claim text (1 sentence)
- "verified": true if the claim is supported by the evidence or document list, false if not
- "source": Brief description of which evidence supports it, or null if unverified

Focus on the most important factual claims and policy citations. Do not verify subjective opinions or recommendations — only factual assertions."""

    # Build ingested document list
    doc_lines = []
    for doc in ingested_documents:
        desc = doc.get("description", "Untitled")
        doc_type = doc.get("document_type", "Unknown")
        url = doc.get("url", "")
        doc_lines.append(f"- {desc} (type: {doc_type}, url: {url})")

    docs_text = "\n".join(doc_lines) if doc_lines else "No documents ingested."

    # Build evidence text
    evidence_lines = []
    for chunk in evidence_chunks[:30]:  # Limit to 30 most relevant chunks
        source = chunk.get("source", "unknown")
        text = chunk.get("text", "")
        meta = chunk.get("metadata", {})
        source_file = meta.get("source_file", meta.get("source", ""))
        evidence_lines.append(f"[{source}:{source_file}] {text}")

    evidence_text = "\n\n---\n\n".join(evidence_lines) if evidence_lines else "No evidence available."

    # Build key findings from structure
    structure_summary_parts = []
    overall = review_structure.get("overall_rating", "unknown")
    structure_summary_parts.append(f"Overall rating: {overall}")

    aspects = review_structure.get("aspects", [])
    for a in aspects:
        name = a.get("name", "")
        rating = a.get("rating", "")
        issue = a.get("key_issue", "")
        structure_summary_parts.append(f"- {name}: {rating} — {issue}")

    structure_text = "\n".join(structure_summary_parts)

    user_prompt = f"""Verify the claims in the following cycling advocacy review against the evidence provided.

## Review to Verify
{review_markdown}

## Structured Assessment
{structure_text}

## Ingested Documents ({len(ingested_documents)} documents)
{docs_text}

## Evidence Chunks ({len(evidence_chunks)} chunks)
{evidence_text}

Extract the key factual claims from the review and verify each one against the evidence and document list. Respond with the JSON object only."""

    return system_prompt, user_prompt

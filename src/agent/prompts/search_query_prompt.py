"""
Search query prompt builder for LLM-based query generation.

Implements [review-workflow-redesign:FR-004] - Dynamic search query generation

Implements:
- [review-workflow-redesign:search_query_prompt/TS-01] Prompt includes proposal context
- [review-workflow-redesign:search_query_prompt/TS-02] Returns structured JSON schema
- [review-workflow-redesign:search_query_prompt/TS-03] Includes ingested document list
"""

from typing import Any


def build_search_query_prompt(
    application_metadata: dict[str, Any],
    ingested_documents: list[dict[str, Any]],
) -> tuple[str, str]:
    """
    Build the system and user prompts for LLM-based search query generation.

    The prompt asks Haiku to generate targeted semantic search queries for
    both application documents and policy documents, tailored to the specific
    planning application.

    Args:
        application_metadata: Dict with keys: reference, address, proposal, type.
        ingested_documents: List of dicts with keys: description, document_type.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    system_prompt = """You are a search query generator for cycling and transport advocacy reviews of planning applications.

Generate targeted semantic search queries to find evidence relevant to assessing the application from a cycling, pedestrian, and sustainable transport perspective.

You MUST respond with a single JSON object and nothing else. No markdown, no commentary — only valid JSON.

The JSON object must have this schema:

{
  "application_queries": [string],
  "policy_queries": [
    {
      "query": string,
      "sources": [string]
    }
  ]
}

**application_queries**: 4-6 search queries for searching the application's ingested documents. These should target:
- Cycle parking provision (quantity, type, location, covered/secure)
- Cycle route design and connectivity to existing network
- Junction design safety for cyclists
- Pedestrian and cycle permeability through the site
- Vehicle access and potential conflicts with cyclists
- Travel plan commitments
Make queries specific to the proposal type (e.g. residential vs commercial vs mixed-use).

**policy_queries**: 3-5 search queries for searching policy documents. Each query has:
- "query": The semantic search query text
- "sources": Array of policy source identifiers to search. Valid sources:
  - "LTN_1_20" (Cycle Infrastructure Design)
  - "NPPF" (National Planning Policy Framework)
  - "CHERWELL_LP_2015" (Cherwell Local Plan)
  - "OCC_LTCP" (Oxfordshire Local Transport and Connectivity Plan)
  - "BICESTER_LCWIP" (Bicester Local Cycling and Walking Infrastructure Plan)
  - "MANUAL_FOR_STREETS" (Manual for Streets)
Target policies that are most relevant to the proposal type and the application queries above."""

    # Build application context
    ref = application_metadata.get("reference", "Unknown")
    address = application_metadata.get("address", "Unknown")
    proposal = application_metadata.get("proposal", "Unknown")
    app_type = application_metadata.get("type", "Unknown")

    # Build ingested document list
    doc_lines = []
    for doc in ingested_documents:
        desc = doc.get("description", "Untitled")
        doc_type = doc.get("document_type", "Unknown")
        doc_lines.append(f"- {desc} (type: {doc_type})")

    docs_text = "\n".join(doc_lines) if doc_lines else "No documents ingested."

    user_prompt = f"""Generate search queries for a cycling advocacy review of this planning application.

## Application
- Reference: {ref}
- Address: {address}
- Proposal: {proposal}
- Type: {app_type}

## Ingested Documents ({len(ingested_documents)} documents)
{docs_text}

Generate targeted queries that will find evidence relevant to cycling and transport aspects of this specific application. Respond with the JSON object only."""

    return system_prompt, user_prompt

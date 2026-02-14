"""
Document filter prompt builder for LLM-based document relevance classification.

Implements [review-workflow-redesign:FR-001] - LLM-based document filtering
Implements [review-workflow-redesign:FR-008] - Application-aware filter context

Implements:
- [review-workflow-redesign:document_filter_prompt/TS-01] Prompt includes application metadata
- [review-workflow-redesign:document_filter_prompt/TS-02] Prompt includes full document list
- [review-workflow-redesign:document_filter_prompt/TS-03] System prompt specifies JSON array output
"""

from typing import Any


def build_document_filter_prompt(
    application_metadata: dict[str, Any],
    document_list: list[dict[str, Any]],
) -> tuple[str, str]:
    """
    Build the system and user prompts for the LLM document filter.

    The filter asks Haiku to classify which documents from a planning application
    are relevant to a cycling/transport advocacy review.

    Args:
        application_metadata: Dict with keys: reference, address, proposal, type.
        document_list: List of dicts with keys: id, description, document_type, date_published.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    system_prompt = """You are a planning document relevance classifier for a cycling and transport advocacy review.

Given a planning application and its document list, identify which documents are relevant to a review focused on cycling infrastructure, pedestrian access, and sustainable transport.

RELEVANT document categories (select these):
- Transport Assessments and Transport Statements
- Travel Plans and Travel Plan Frameworks
- Design and Access Statements
- Site layout plans and masterplans
- Highway and access design drawings
- Officer/committee reports and decision notices
- Application forms (contain proposal description)
- Planning statements and supporting statements
- Environmental statements (transport chapters)
- Infrastructure and s106/s278 documents
- Flood risk assessments (if they discuss access routes)
- Landscape plans (if they show pedestrian/cycle routes)
- Block plans and location plans

NOT RELEVANT (exclude these):
- Ecology/biodiversity reports and surveys
- Heritage/archaeological assessments
- Arboricultural reports and tree surveys
- Noise and air quality assessments
- Contamination and geotechnical reports
- Energy/sustainability statements (unless transport-focused)
- Drainage strategies and surface water management
- Consultation responses and public comments
- Neighbour notification letters
- Affordable housing statements
- Viability assessments
- Photo montages and CGI visualisations

RULES:
1. When a document name is ambiguous or generic (e.g. "Document 1", "Supporting Information"), INCLUDE it
2. For small applications (under 10 documents), lean towards including more documents
3. For large applications (50+ documents), be more selective
4. Always include the application form and any officer/committee reports

You MUST respond with ONLY a JSON array of document ID strings. No commentary, no explanation — only valid JSON.

Example response:
["doc_001", "doc_005", "doc_012"]"""

    # Build application context
    ref = application_metadata.get("reference", "Unknown")
    address = application_metadata.get("address", "Unknown")
    proposal = application_metadata.get("proposal", "Unknown")
    app_type = application_metadata.get("type", "Unknown")

    # Build document list text
    doc_lines = []
    for doc in document_list:
        doc_id = doc.get("id", "")
        desc = doc.get("description", "Untitled")
        doc_type = doc.get("document_type", "Unknown")
        date = doc.get("date_published", "")
        line = f"- ID: {doc_id} | Description: {desc} | Type: {doc_type}"
        if date:
            line += f" | Date: {date}"
        doc_lines.append(line)

    docs_text = "\n".join(doc_lines) if doc_lines else "No documents listed."

    user_prompt = f"""Select the relevant documents for a cycling/transport advocacy review of this planning application.

## Application
- Reference: {ref}
- Address: {address}
- Proposal: {proposal}
- Type: {app_type}

## Document List ({len(document_list)} documents)
{docs_text}

Respond with a JSON array of the relevant document IDs only."""

    return system_prompt, user_prompt

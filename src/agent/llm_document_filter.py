"""
LLM-based document relevance filter for planning application reviews.

Uses a fast, cheap LLM call to assess which documents from a scraped
document list are actually relevant to a cycling advocacy review, before
downloading them.  This replaces the previous approach of downloading
hundreds of documents and filtering afterwards.

The filter runs between the document-listing and document-downloading
phases of the orchestrator workflow.
"""

import json
import os
from dataclasses import dataclass, field

import anthropic
import structlog

logger = structlog.get_logger(__name__)

# Fast / cheap model for filtering — configurable via env
DEFAULT_FILTER_MODEL = "claude-haiku-3-5-20241022"

SYSTEM_PROMPT = """\
You are a document relevance filter for a cycling advocacy group that reviews \
planning applications submitted to Cherwell District Council.

Given a planning application description and a list of associated documents, \
select ONLY the documents that are relevant to assessing the application from \
a cycling, walking, and active travel perspective.

ALWAYS include documents such as:
- Transport Assessments / Transport Statements
- Travel Plans
- Highway / access / junction designs
- Site plans and layout plans (including proposed and existing)
- Design and Access Statements
- Planning Statements
- Location plans and block plans
- Officer reports and decision notices
- Proposed plans/drawings showing road, path, or access layouts
- Parking strategies and parking plans
- Any document explicitly mentioning cycling, walking, or active travel
- Masterplans and parameter plans
- S106 agreements and legal agreements
- Application forms
- Covering letters that summarise the proposal

NEVER include documents such as:
- Ecology / biodiversity reports
- Arboricultural / tree surveys
- Heritage / archaeological assessments
- Flood risk / drainage / SUDs assessments
- Noise / acoustic reports
- Air quality assessments
- Energy / sustainability statements
- Ground contamination / geotechnical reports
- Public comments, objection letters, or representations from the public
- Landscape and visual impact assessments (unless they show path/route layouts)
- Lighting assessments
- Utility / service plans

For ambiguous documents, INCLUDE them — it is better to download a few extra \
documents than to miss something relevant.

Respond with ONLY a JSON object (no markdown fences, no explanation) in this format:
{
  "selected": [
    {"document_id": "...", "reason": "brief reason"}
  ],
  "excluded": [
    {"document_id": "...", "reason": "brief reason"}
  ]
}
"""


@dataclass
class FilteredDocument:
    """A document selected or excluded by the LLM filter."""

    document_id: str
    reason: str


@dataclass
class LLMFilterResult:
    """Result of LLM document filtering."""

    selected: list[FilteredDocument] = field(default_factory=list)
    excluded: list[FilteredDocument] = field(default_factory=list)
    model_used: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    fallback_used: bool = False


class LLMDocumentFilter:
    """
    Filters a scraped document list using an LLM call to identify
    documents relevant to a cycling advocacy planning review.

    Falls back to allowing all documents if the LLM call fails.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self._model = model or os.getenv("CLAUDE_FILTER_MODEL", DEFAULT_FILTER_MODEL)

        if not self._api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

    async def filter_documents(
        self,
        documents: list[dict],
        application_summary: str = "",
    ) -> LLMFilterResult:
        """
        Filter a list of documents for relevance to cycling advocacy review.

        Args:
            documents: List of document dicts, each with at minimum
                       ``document_id``, ``description``, and optionally
                       ``document_type``.
            application_summary: Human-readable summary of the planning
                                 application (address, proposal, etc.).

        Returns:
            LLMFilterResult with selected/excluded documents.
            On failure, returns all documents as selected (fail-safe).
        """
        if not documents:
            return LLMFilterResult()

        # Build the user prompt
        doc_lines = []
        for doc in documents:
            doc_id = doc.get("document_id", "unknown")
            desc = doc.get("description", "No description")
            doc_type = doc.get("document_type", "Unknown")
            date = doc.get("date_published", "")
            line = f"- id={doc_id} | type={doc_type} | description={desc}"
            if date:
                line += f" | date={date}"
            doc_lines.append(line)

        user_prompt = (
            f"## Planning Application\n{application_summary}\n\n"
            f"## Documents ({len(documents)} total)\n"
            + "\n".join(doc_lines)
        )

        try:
            client = anthropic.Anthropic(api_key=self._api_key)
            response = client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )

            raw_text = response.content[0].text.strip()

            # Strip markdown code fence if present
            if raw_text.startswith("```"):
                lines = raw_text.split("\n")
                lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                raw_text = "\n".join(lines)

            parsed = json.loads(raw_text)

            result = LLMFilterResult(
                model_used=self._model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )

            for item in parsed.get("selected", []):
                result.selected.append(FilteredDocument(
                    document_id=item.get("document_id", ""),
                    reason=item.get("reason", ""),
                ))

            for item in parsed.get("excluded", []):
                result.excluded.append(FilteredDocument(
                    document_id=item.get("document_id", ""),
                    reason=item.get("reason", ""),
                ))

            logger.info(
                "LLM document filter complete",
                total_documents=len(documents),
                selected=len(result.selected),
                excluded=len(result.excluded),
                model=self._model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )

            return result

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(
                "LLM filter returned unparseable response, falling back to allow-all",
                error=str(e),
            )
            return self._fallback_allow_all(documents)

        except anthropic.APIError as e:
            logger.warning(
                "LLM filter API error, falling back to allow-all",
                error=str(e),
            )
            return self._fallback_allow_all(documents)

        except Exception as e:
            logger.exception(
                "Unexpected error in LLM filter, falling back to allow-all",
                error=str(e),
            )
            return self._fallback_allow_all(documents)

    @staticmethod
    def _fallback_allow_all(documents: list[dict]) -> LLMFilterResult:
        """Fail-safe: select all documents when LLM filter fails."""
        return LLMFilterResult(
            selected=[
                FilteredDocument(
                    document_id=doc.get("document_id", "unknown"),
                    reason="LLM filter unavailable - included by default",
                )
                for doc in documents
            ],
            fallback_used=True,
        )

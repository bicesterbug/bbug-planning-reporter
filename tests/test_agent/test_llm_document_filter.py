"""
Tests for LLM-based document relevance filter.

Verifies that the LLMDocumentFilter correctly:
- Sends document lists to the LLM and parses responses
- Falls back to allow-all on API errors
- Falls back to allow-all on malformed JSON responses
- Handles empty document lists
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.agent.llm_document_filter import (
    LLMDocumentFilter,
    LLMFilterResult,
)


@pytest.fixture
def sample_documents():
    """Sample document list as returned by list_application_documents."""
    return [
        {
            "document_id": "doc1",
            "description": "Transport Assessment",
            "document_type": "Supporting Documents",
            "date_published": "2025-01-20",
        },
        {
            "document_id": "doc2",
            "description": "Ecological Impact Assessment",
            "document_type": "Supporting Documents",
            "date_published": "2025-01-20",
        },
        {
            "document_id": "doc3",
            "description": "Design and Access Statement",
            "document_type": "Supporting Documents",
        },
        {
            "document_id": "doc4",
            "description": "Flood Risk Assessment",
            "document_type": "Supporting Documents",
        },
        {
            "document_id": "doc5",
            "description": "Site Plan",
            "document_type": "Application Forms",
        },
    ]


@pytest.fixture
def llm_filter():
    """Create an LLMDocumentFilter with a test API key."""
    return LLMDocumentFilter(api_key="test-key", model="claude-haiku-3-5-20241022")


def _make_anthropic_response(text: str, input_tokens: int = 100, output_tokens: int = 200):
    """Build a mock Anthropic Messages response."""
    content_block = SimpleNamespace(text=text)
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    return SimpleNamespace(content=[content_block], usage=usage)


class TestLLMDocumentFilter:
    """Tests for LLMDocumentFilter."""

    @pytest.mark.asyncio
    @patch("src.agent.llm_document_filter.anthropic.Anthropic")
    async def test_successful_filter(
        self, mock_anthropic_cls, llm_filter, sample_documents
    ):
        """LLM selects relevant documents and excludes irrelevant ones."""
        response_json = json.dumps({
            "selected": [
                {"document_id": "doc1", "reason": "Transport assessment is core"},
                {"document_id": "doc3", "reason": "DAS contains layout info"},
                {"document_id": "doc5", "reason": "Site plan shows access"},
            ],
            "excluded": [
                {"document_id": "doc2", "reason": "Ecology not relevant"},
                {"document_id": "doc4", "reason": "Flood risk not relevant"},
            ],
        })

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_anthropic_response(response_json)
        mock_anthropic_cls.return_value = mock_client

        result = await llm_filter.filter_documents(
            sample_documents, application_summary="Residential development"
        )

        assert len(result.selected) == 3
        assert len(result.excluded) == 2
        assert not result.fallback_used
        assert result.model_used == "claude-haiku-3-5-20241022"
        assert result.input_tokens == 100
        assert result.output_tokens == 200

        selected_ids = {d.document_id for d in result.selected}
        assert selected_ids == {"doc1", "doc3", "doc5"}

    @pytest.mark.asyncio
    @patch("src.agent.llm_document_filter.anthropic.Anthropic")
    async def test_filter_with_markdown_code_fence(
        self, mock_anthropic_cls, llm_filter, sample_documents
    ):
        """LLM response wrapped in markdown code fences is handled."""
        raw = json.dumps({
            "selected": [{"document_id": "doc1", "reason": "relevant"}],
            "excluded": [{"document_id": "doc2", "reason": "not relevant"}],
        })
        fenced = f"```json\n{raw}\n```"

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_anthropic_response(fenced)
        mock_anthropic_cls.return_value = mock_client

        result = await llm_filter.filter_documents(sample_documents)

        assert len(result.selected) == 1
        assert result.selected[0].document_id == "doc1"
        assert not result.fallback_used

    @pytest.mark.asyncio
    @patch("src.agent.llm_document_filter.anthropic.Anthropic")
    async def test_fallback_on_invalid_json(
        self, mock_anthropic_cls, llm_filter, sample_documents
    ):
        """Falls back to allow-all when LLM returns unparseable text."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_anthropic_response(
            "Sorry, I cannot parse these documents."
        )
        mock_anthropic_cls.return_value = mock_client

        result = await llm_filter.filter_documents(sample_documents)

        assert result.fallback_used
        assert len(result.selected) == len(sample_documents)
        assert len(result.excluded) == 0

    @pytest.mark.asyncio
    @patch("src.agent.llm_document_filter.anthropic.Anthropic")
    async def test_fallback_on_api_error(
        self, mock_anthropic_cls, llm_filter, sample_documents
    ):
        """Falls back to allow-all when Anthropic API raises an error."""
        import anthropic as anthropic_mod

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = anthropic_mod.APIError(
            message="Service unavailable",
            request=MagicMock(),
            body=None,
        )
        mock_anthropic_cls.return_value = mock_client

        result = await llm_filter.filter_documents(sample_documents)

        assert result.fallback_used
        assert len(result.selected) == len(sample_documents)

    @pytest.mark.asyncio
    async def test_empty_document_list(self, llm_filter):
        """Empty document list returns empty result without calling LLM."""
        result = await llm_filter.filter_documents([])

        assert len(result.selected) == 0
        assert len(result.excluded) == 0
        assert not result.fallback_used

    @pytest.mark.asyncio
    @patch("src.agent.llm_document_filter.anthropic.Anthropic")
    async def test_application_summary_included_in_prompt(
        self, mock_anthropic_cls, llm_filter, sample_documents
    ):
        """Application summary is passed to the LLM in the user prompt."""
        response_json = json.dumps({
            "selected": [{"document_id": "doc1", "reason": "relevant"}],
            "excluded": [],
        })
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_anthropic_response(response_json)
        mock_anthropic_cls.return_value = mock_client

        await llm_filter.filter_documents(
            sample_documents,
            application_summary="Reference: 25/01178/REM\nProposal: Housing development",
        )

        call_args = mock_client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        assert "25/01178/REM" in user_content
        assert "Housing development" in user_content

    @pytest.mark.asyncio
    @patch("src.agent.llm_document_filter.anthropic.Anthropic")
    async def test_all_documents_in_prompt(
        self, mock_anthropic_cls, llm_filter, sample_documents
    ):
        """All document IDs and descriptions appear in the LLM prompt."""
        response_json = json.dumps({"selected": [], "excluded": []})
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_anthropic_response(response_json)
        mock_anthropic_cls.return_value = mock_client

        await llm_filter.filter_documents(sample_documents)

        call_args = mock_client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        for doc in sample_documents:
            assert doc["document_id"] in user_content
            assert doc["description"] in user_content

    def test_missing_api_key_raises(self):
        """Raises ValueError when no API key is available."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
                LLMDocumentFilter(api_key=None)


class TestLLMFilterResult:
    """Tests for the LLMFilterResult dataclass."""

    def test_default_values(self):
        """Default result has empty lists and no fallback."""
        result = LLMFilterResult()
        assert result.selected == []
        assert result.excluded == []
        assert result.model_used == ""
        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert not result.fallback_used

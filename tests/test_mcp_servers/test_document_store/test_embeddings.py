"""
Tests for EmbeddingService.

Implements test scenarios from [document-processing:EmbeddingService/TS-01] through [TS-06]
"""

import numpy as np
import pytest

from src.mcp_servers.document_store.embeddings import (
    EmbeddingService,
    MockEmbeddingModel,
)


@pytest.fixture
def mock_model() -> MockEmbeddingModel:
    """Create a mock embedding model."""
    return MockEmbeddingModel(dim=384)


@pytest.fixture
def service(mock_model: MockEmbeddingModel) -> EmbeddingService:
    """Create an EmbeddingService with mock model."""
    return EmbeddingService(model=mock_model)


class TestGenerateSingleEmbedding:
    """Tests for single embedding generation."""

    def test_generate_single_embedding(self, service: EmbeddingService) -> None:
        """
        Verifies [document-processing:EmbeddingService/TS-01]

        Given: Text chunk
        When: Call embed()
        Then: Returns 384-dimensional vector
        """
        text = "This is a sample text for embedding."
        embedding = service.embed(text)

        assert isinstance(embedding, list)
        assert len(embedding) == 384
        assert all(isinstance(v, float) for v in embedding)

    def test_embedding_is_normalized(self, service: EmbeddingService) -> None:
        """
        Given: Text chunk
        When: Call embed()
        Then: Returns normalized (unit) vector
        """
        text = "Sample text for normalization test."
        embedding = service.embed(text)

        # Check that the vector is approximately unit length
        magnitude = np.sqrt(sum(v**2 for v in embedding))
        assert abs(magnitude - 1.0) < 0.01


class TestBatchEmbedding:
    """Tests for batch embedding generation."""

    def test_batch_embedding(self, service: EmbeddingService) -> None:
        """
        Verifies [document-processing:EmbeddingService/TS-02]

        Given: List of 32 chunks
        When: Call embed_batch()
        Then: Returns list of 32 embeddings efficiently
        """
        texts = [f"Sample text number {i}" for i in range(32)]
        embeddings = service.embed_batch(texts)

        assert len(embeddings) == 32
        for emb in embeddings:
            assert len(emb) == 384

    def test_empty_batch_returns_empty_list(self, service: EmbeddingService) -> None:
        """
        Given: Empty list
        When: Call embed_batch()
        Then: Returns empty list
        """
        embeddings = service.embed_batch([])
        assert embeddings == []


class TestEmbeddingConsistency:
    """Tests for embedding consistency."""

    def test_embedding_consistency(self, service: EmbeddingService) -> None:
        """
        Verifies [document-processing:EmbeddingService/TS-03]

        Given: Same text twice
        When: Call embed() twice
        Then: Produces identical vectors
        """
        text = "This text should produce the same embedding each time."

        embedding1 = service.embed(text)
        embedding2 = service.embed(text)

        assert embedding1 == embedding2

    def test_different_texts_produce_different_embeddings(
        self, service: EmbeddingService
    ) -> None:
        """
        Given: Different texts
        When: Call embed()
        Then: Produces different vectors
        """
        text1 = "First unique text sample."
        text2 = "Second completely different text."

        embedding1 = service.embed(text1)
        embedding2 = service.embed(text2)

        # Embeddings should be different
        assert embedding1 != embedding2


class TestHandleEmptyText:
    """Tests for empty text handling."""

    def test_handle_empty_text(self, service: EmbeddingService) -> None:
        """
        Verifies [document-processing:EmbeddingService/TS-04]

        Given: Empty string
        When: Call embed()
        Then: Raises ValueError
        """
        with pytest.raises(ValueError) as exc_info:
            service.embed("")

        assert "Cannot embed empty text" in str(exc_info.value)

    def test_handle_whitespace_only(self, service: EmbeddingService) -> None:
        """
        Given: Whitespace only string
        When: Call embed()
        Then: Raises ValueError
        """
        with pytest.raises(ValueError):
            service.embed("   \n\t   ")

    def test_batch_with_empty_text_raises(self, service: EmbeddingService) -> None:
        """
        Given: Batch with one empty text
        When: Call embed_batch()
        Then: Raises ValueError
        """
        texts = ["Valid text", "", "Another valid text"]

        with pytest.raises(ValueError) as exc_info:
            service.embed_batch(texts)

        assert "index 1" in str(exc_info.value)


class TestHandleLongText:
    """Tests for long text handling."""

    def test_handle_very_long_text(self, service: EmbeddingService) -> None:
        """
        Verifies [document-processing:EmbeddingService/TS-05]

        Given: Text exceeding model max length
        When: Call embed()
        Then: Truncates appropriately, logs warning
        """
        # Create very long text
        long_text = "word " * 10000  # ~50000 characters

        # Should not raise, but truncate
        embedding = service.embed(long_text)

        assert len(embedding) == 384


class TestModelLoading:
    """Tests for model loading behavior."""

    def test_model_loading_lazy(self) -> None:
        """
        Verifies [document-processing:EmbeddingService/TS-06]

        Given: First call to service
        When: Call embed()
        Then: Model loaded lazily
        """
        mock = MockEmbeddingModel()
        service = EmbeddingService(model=mock)

        # Model should be marked as loaded since we provided it
        assert service.is_loaded

    def test_service_without_model_not_loaded(self) -> None:
        """
        Given: Service created without model
        When: Check is_loaded
        Then: Returns False until first use
        """
        service = EmbeddingService(model=None)
        assert not service.is_loaded

    def test_embedding_dim_property(self, service: EmbeddingService) -> None:
        """Test embedding dimension property."""
        assert service.embedding_dim == 384


class TestMockEmbeddingModel:
    """Tests for the MockEmbeddingModel itself."""

    def test_mock_produces_correct_dimensions(self) -> None:
        """Mock should produce correct embedding dimensions."""
        mock = MockEmbeddingModel(dim=384)
        embeddings = mock.encode(["test text"])

        assert embeddings.shape == (1, 384)

    def test_mock_deterministic(self) -> None:
        """Mock should be deterministic for same input."""
        mock = MockEmbeddingModel()
        text = "Test text for determinism."

        emb1 = mock.encode([text])
        emb2 = mock.encode([text])

        np.testing.assert_array_almost_equal(emb1, emb2)

    def test_mock_handles_string_input(self) -> None:
        """Mock should handle single string input."""
        mock = MockEmbeddingModel()
        embedding = mock.encode("single string")

        assert embedding.shape == (1, 384)

    def test_mock_handles_list_input(self) -> None:
        """Mock should handle list of strings."""
        mock = MockEmbeddingModel()
        embeddings = mock.encode(["text1", "text2", "text3"])

        assert embeddings.shape == (3, 384)

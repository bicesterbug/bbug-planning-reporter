"""
Embedding generation service for document processing.

Implements [document-processing:FR-004] - Generate embeddings with all-MiniLM-L6-v2
Implements [document-processing:NFR-003] - Embedding consistency
"""

from typing import Protocol

import numpy as np
import structlog

logger = structlog.get_logger(__name__)


class EmbeddingModel(Protocol):
    """Protocol for embedding models."""

    def encode(
        self,
        sentences: list[str] | str,
        batch_size: int = 32,
        show_progress_bar: bool = False,
    ) -> np.ndarray:
        """Encode sentences to embeddings."""
        ...


class EmbeddingService:
    """
    Generates vector embeddings for text chunks.

    Uses sentence-transformers library with the all-MiniLM-L6-v2 model
    which produces 384-dimensional embeddings.

    Implements:
    - [document-processing:EmbeddingService/TS-01] Generate single embedding
    - [document-processing:EmbeddingService/TS-02] Batch embedding
    - [document-processing:EmbeddingService/TS-03] Embedding consistency
    - [document-processing:EmbeddingService/TS-04] Handle empty text
    - [document-processing:EmbeddingService/TS-05] Handle very long text
    - [document-processing:EmbeddingService/TS-06] Model loading
    """

    MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
    EMBEDDING_DIM = 384
    MAX_SEQUENCE_LENGTH = 256  # Model's maximum input length

    def __init__(self, model: EmbeddingModel | None = None) -> None:
        """
        Initialize the embedding service.

        Args:
            model: Optional pre-loaded model for testing. If None, loads lazily.
        """
        self._model: EmbeddingModel | None = model
        self._model_loaded = model is not None

    def _load_model(self) -> EmbeddingModel:
        """Lazy load the embedding model."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                logger.info("Loading embedding model", model=self.MODEL_NAME)
                self._model = SentenceTransformer(self.MODEL_NAME)
                self._model_loaded = True
                logger.info("Embedding model loaded successfully")
            except ImportError:
                raise RuntimeError(
                    "sentence-transformers not installed. "
                    "Install with: pip install sentence-transformers"
                )
        return self._model

    def embed(self, text: str) -> list[float]:
        """
        Generate embedding for a single text.

        Args:
            text: The text to embed.

        Returns:
            384-dimensional embedding vector.

        Raises:
            ValueError: If text is empty.
        """
        if not text.strip():
            raise ValueError("Cannot embed empty text")

        model = self._load_model()

        # Truncate if too long
        if len(text) > self.MAX_SEQUENCE_LENGTH * 4:  # Rough char estimate
            logger.warning(
                "Text exceeds max length, truncating",
                original_length=len(text),
                max_length=self.MAX_SEQUENCE_LENGTH * 4,
            )
            text = text[: self.MAX_SEQUENCE_LENGTH * 4]

        embedding = model.encode([text], batch_size=1, show_progress_bar=False)
        return embedding[0].tolist()

    def embed_batch(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        """
        Generate embeddings for multiple texts efficiently.

        Args:
            texts: List of texts to embed.
            batch_size: Number of texts to process at once.

        Returns:
            List of 384-dimensional embedding vectors.

        Raises:
            ValueError: If any text is empty.
        """
        if not texts:
            return []

        # Validate and truncate texts
        processed_texts = []
        for i, text in enumerate(texts):
            if not text.strip():
                raise ValueError(f"Cannot embed empty text at index {i}")

            if len(text) > self.MAX_SEQUENCE_LENGTH * 4:
                logger.warning(
                    "Text exceeds max length, truncating",
                    index=i,
                    original_length=len(text),
                )
                text = text[: self.MAX_SEQUENCE_LENGTH * 4]
            processed_texts.append(text)

        model = self._load_model()

        logger.debug(
            "Generating batch embeddings",
            count=len(processed_texts),
            batch_size=batch_size,
        )

        embeddings = model.encode(
            processed_texts,
            batch_size=batch_size,
            show_progress_bar=False,
        )

        return [e.tolist() for e in embeddings]

    @property
    def embedding_dim(self) -> int:
        """Get the embedding dimensionality."""
        return self.EMBEDDING_DIM

    @property
    def is_loaded(self) -> bool:
        """Check if the model is loaded."""
        return self._model_loaded


class MockEmbeddingModel:
    """Mock embedding model for testing without loading actual model."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim
        self._call_count = 0

    def encode(
        self,
        sentences: list[str] | str,
        batch_size: int = 32,  # noqa: ARG002 - kept for Protocol compatibility
        show_progress_bar: bool = False,  # noqa: ARG002 - kept for Protocol compatibility
    ) -> np.ndarray:
        """Generate deterministic mock embeddings based on text content."""
        if isinstance(sentences, str):
            sentences = [sentences]

        embeddings = []
        for text in sentences:
            # Create deterministic embedding based on text hash
            # This ensures same text produces same embedding
            seed = hash(text) % (2**32)
            rng = np.random.RandomState(seed)
            embedding = rng.randn(self.dim).astype(np.float32)
            # Normalize to unit vector
            embedding = embedding / np.linalg.norm(embedding)
            embeddings.append(embedding)

        self._call_count += 1
        return np.array(embeddings)

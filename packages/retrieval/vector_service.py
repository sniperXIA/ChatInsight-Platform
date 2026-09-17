import hashlib
import logging
from datetime import datetime
from typing import Optional, Sequence
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.model_gateway.gateway import ModelGateway
from packages.model_gateway.settings_manager import SettingsManager
from packages.persistence.models import (
    Insight,
    MessageContextChunk,
    Topic,
    TopicVector,
)
from packages.retrieval.vector_math import (
    cosine_similarity,
    l2_normalize,
    pack_vector,
    unpack_vector,
)

logger = logging.getLogger(__name__)


class VectorService:
    """
    High-level service managing text embeddings, vector caching,
    database persistence, and semantic similarity search.
    """

    def __init__(self, session: AsyncSession, model_gateway: ModelGateway | None = None):
        self.session = session
        self.gateway = model_gateway or ModelGateway()
        self._memory_cache: dict[tuple[str, str], list[float]] = {}

    @classmethod
    def compute_sha256(cls, text: str) -> str:
        return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()

    async def embed_texts(
        self,
        texts: list[str],
        model: str | None = None,
        force_mock: bool = False,
    ) -> list[list[float]]:
        """
        Embeds a list of texts into L2-normalized float vectors.
        Utilizes in-memory SHA256 cache to avoid duplicate API calls.
        """
        if not texts:
            return []

        settings = SettingsManager.get_settings()
        embedding_cfg = getattr(settings, "embedding", None)
        selected_model = model or (embedding_cfg.model if embedding_cfg else "text-embedding-v3")

        results: list[Optional[list[float]]] = [None] * len(texts)
        missing_indices: list[int] = []
        missing_texts: list[str] = []

        for idx, t in enumerate(texts):
            clean_t = (t or "").strip()
            cache_key = (self.compute_sha256(clean_t), selected_model)
            if cache_key in self._memory_cache:
                results[idx] = self._memory_cache[cache_key]
            else:
                missing_indices.append(idx)
                missing_texts.append(clean_t if clean_t else "<empty>")

        if missing_texts:
            provider = self.gateway.get_embedding_provider(force_mock=force_mock)
            try:
                raw_vectors, _ = await provider.generate_embeddings(
                    texts=missing_texts,
                    model=selected_model,
                )
            except Exception as exc:
                logger.warning(f"Embedding generation failed: {exc}. Falling back to mock generator.")
                mock_provider = self.gateway.get_embedding_provider(force_mock=True)
                raw_vectors, _ = await mock_provider.generate_embeddings(
                    texts=missing_texts,
                    model=selected_model,
                )

            for local_idx, raw_vec in enumerate(raw_vectors):
                orig_idx = missing_indices[local_idx]
                norm_vec = l2_normalize(raw_vec)
                results[orig_idx] = norm_vec
                cache_key = (self.compute_sha256(texts[orig_idx]), selected_model)
                self._memory_cache[cache_key] = norm_vec

        return [v if v is not None else [0.0] * 1536 for v in results]

    async def embed_single(
        self,
        text: str,
        model: str | None = None,
        force_mock: bool = False,
    ) -> list[float]:
        """Embeds a single string into a normalized vector."""
        vecs = await self.embed_texts([text], model=model, force_mock=force_mock)
        return vecs[0] if vecs else []

    async def get_or_create_topic_vector(
        self,
        topic: Topic,
        force_mock: bool = False,
    ) -> list[float]:
        """
        Gets existing TopicVector or embeds and persists a new one for the given Topic.
        """
        settings = SettingsManager.get_settings()
        embedding_cfg = getattr(settings, "embedding", None)
        selected_model = embedding_cfg.model if embedding_cfg else "text-embedding-v3"

        text_repr = f"{topic.module or ''} · {topic.sub_module or ''}\n标题：{topic.title or ''}\n摘要：{topic.summary or ''}"
        text_hash = self.compute_sha256(text_repr)

        stmt = select(TopicVector).where(
            TopicVector.topic_id == topic.id,
            TopicVector.embedding_model == selected_model,
        )
        existing = (await self.session.execute(stmt)).scalar_one_or_none()

        if existing and existing.text_sha256 == text_hash and existing.embedding:
            return unpack_vector(existing.embedding)

        vec = await self.embed_single(text_repr, model=selected_model, force_mock=force_mock)
        blob = pack_vector(vec)

        if existing:
            existing.text_repr = text_repr
            existing.text_sha256 = text_hash
            existing.embedding = blob
            existing.dimension = len(vec)
            existing.updated_at = datetime.now()
        else:
            new_tv = TopicVector(
                workspace_id=topic.workspace_id,
                topic_id=topic.id,
                text_repr=text_repr,
                text_sha256=text_hash,
                embedding=blob,
                embedding_model=selected_model,
                dimension=len(vec),
            )
            self.session.add(new_tv)

        await self.session.flush()
        return vec

    async def get_insight_vector(
        self,
        insight: Insight,
        force_mock: bool = False,
    ) -> list[float]:
        """
        Generates dense vector representation for an Insight.
        """
        settings = SettingsManager.get_settings()
        embedding_cfg = getattr(settings, "embedding", None)
        selected_model = embedding_cfg.model if embedding_cfg else "text-embedding-v3"

        text_repr = f"{insight.module or ''} · {insight.sub_module or ''}\n{insight.summary or ''}\n{insight.description or ''}"
        return await self.embed_single(text_repr, model=selected_model, force_mock=force_mock)

    async def find_similar_topics(
        self,
        query_vector: list[float],
        candidate_topics: Sequence[Topic],
        min_threshold: float = 0.25,
        force_mock: bool = False,
    ) -> list[tuple[Topic, float]]:
        """
        Calculates cosine similarity between a query vector and candidate topics,
        returning descending sorted (Topic, score) pairs.
        """
        if not candidate_topics or not query_vector:
            return []

        scored: list[tuple[Topic, float]] = []
        for topic in candidate_topics:
            t_vec = await self.get_or_create_topic_vector(topic, force_mock=force_mock)
            sim = cosine_similarity(query_vector, t_vec)
            if sim >= min_threshold:
                scored.append((topic, round(sim, 4)))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

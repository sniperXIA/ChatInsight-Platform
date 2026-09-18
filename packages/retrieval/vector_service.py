import hashlib
import logging
from datetime import datetime
from typing import Any, Optional, Sequence
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.model_gateway.gateway import ModelGateway
from packages.model_gateway.settings_manager import SettingsManager
from packages.persistence.models import (
    Insight,
    Message,
    MessageContextChunk,
    Participant,
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

    async def index_message_chunks(
        self,
        conversation_id: str,
        messages: Optional[Sequence[Message]] = None,
        window_size: int = 4,
        force_mock: bool = False,
    ) -> list[MessageContextChunk]:
        """
        Builds and persists sliding window MessageContextChunk items for a conversation.
        """
        settings = SettingsManager.get_settings()
        embedding_cfg = getattr(settings, "embedding", None)
        selected_model = embedding_cfg.model if embedding_cfg else "text-embedding-v3"

        if messages is None:
            msg_stmt = (
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.sent_at.asc())
            )
            msg_res = await self.session.execute(msg_stmt)
            msgs = list(msg_res.scalars().all())
        else:
            msgs = list(messages)

        if not msgs:
            return []

        # Map participants for readable speaker labels
        p_stmt = select(Participant).where(Participant.workspace_id == msgs[0].workspace_id)
        p_res = await self.session.execute(p_stmt)
        part_map = {p.id: p.display_label for p in p_res.scalars().all()}

        created_chunks: list[MessageContextChunk] = []
        step = max(1, window_size // 2)

        for i in range(0, len(msgs), step):
            win = msgs[i : i + window_size]
            if not win:
                continue

            center_msg = win[len(win) // 2]
            lines = []
            for m in win:
                speaker = part_map.get(m.participant_id, "用户")
                t_str = m.sent_at.strftime("%H:%M") if m.sent_at else ""
                lines.append(f"[{t_str}] {speaker}: {m.raw_text.strip()}")

            context_text = "\n".join(lines)
            text_hash = self.compute_sha256(context_text)

            stmt = select(MessageContextChunk).where(
                MessageContextChunk.center_message_id == center_msg.id,
                MessageContextChunk.embedding_model == selected_model,
            )
            existing = (await self.session.execute(stmt)).scalar_one_or_none()

            if existing:
                if existing.text_sha256 != text_hash:
                    vec = await self.embed_single(context_text, model=selected_model, force_mock=force_mock)
                    existing.context_text = context_text
                    existing.text_sha256 = text_hash
                    existing.embedding = pack_vector(vec)
                    existing.dimension = len(vec)
                created_chunks.append(existing)
            else:
                vec = await self.embed_single(context_text, model=selected_model, force_mock=force_mock)
                new_chunk = MessageContextChunk(
                    workspace_id=center_msg.workspace_id,
                    conversation_id=conversation_id,
                    center_message_id=center_msg.id,
                    context_text=context_text,
                    text_sha256=text_hash,
                    embedding=pack_vector(vec),
                    embedding_model=selected_model,
                    dimension=len(vec),
                )
                self.session.add(new_chunk)
                created_chunks.append(new_chunk)

        await self.session.flush()
        return created_chunks

    async def search_dense_candidates(
        self,
        query: str,
        limit: int = 20,
        entity_types: Sequence[str] = ("topic", "insight", "chunk"),
        min_score: float = 0.20,
        force_mock: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Executes first-stage dense semantic vector search across topics, insights, and message chunks.
        Guarantees high-recall semantic matches even when lexical token overlap is zero.
        """
        clean_q = (query or "").strip()
        if not clean_q:
            return []

        settings = SettingsManager.get_settings()
        embedding_cfg = getattr(settings, "embedding", None)
        if embedding_cfg and not embedding_cfg.enabled and not force_mock:
            return []

        selected_model = embedding_cfg.model if embedding_cfg else "text-embedding-v3"
        query_vec = await self.embed_single(clean_q, model=selected_model, force_mock=force_mock)
        if not query_vec:
            return []

        candidates: list[dict[str, Any]] = []

        # 1. Search Topics via TopicVector
        if "topic" in entity_types:
            topic_stmt = select(Topic).order_by(desc(Topic.feedback_count)).limit(60)
            topics = (await self.session.execute(topic_stmt)).scalars().all()
            for t in topics:
                t_vec = await self.get_or_create_topic_vector(t, force_mock=force_mock)
                sim = cosine_similarity(query_vec, t_vec)
                if sim >= min_score:
                    candidates.append({
                        "entity_type": "topic",
                        "entity_id": t.id,
                        "title": t.title,
                        "snippet": t.summary[:180],
                        "score": round(sim, 4),
                        "evidence_uri": f"chatinsight://topics/{t.id}",
                        "metadata": {
                            "module": t.module,
                            "severity": t.severity,
                            "status": t.status,
                            "feedback_count": t.feedback_count,
                        },
                        "object": t,
                    })

        # 2. Search Insights via insight representations
        if "insight" in entity_types:
            ins_stmt = select(Insight).order_by(desc(Insight.confidence)).limit(60)
            insights = (await self.session.execute(ins_stmt)).scalars().all()
            for ins in insights:
                ins_vec = await self.get_insight_vector(ins, force_mock=force_mock)
                sim = cosine_similarity(query_vec, ins_vec)
                if sim >= min_score:
                    candidates.append({
                        "entity_type": "insight",
                        "entity_id": ins.id,
                        "title": f"[{ins.insight_type.upper()}] {ins.summary}",
                        "snippet": ins.description[:180],
                        "score": round(sim, 4),
                        "evidence_uri": f"chatinsight://insights/{ins.id}",
                        "metadata": {
                            "module": ins.module,
                            "severity": ins.severity,
                            "status_in_chat": ins.status_in_chat,
                            "support_known": ins.support_known_status,
                        },
                        "object": ins,
                    })

        # 3. Search MessageContextChunk
        if "chunk" in entity_types:
            chunk_stmt = select(MessageContextChunk).limit(80)
            chunks = (await self.session.execute(chunk_stmt)).scalars().all()

            # If no chunks yet but messages exist, auto-index up to 30 messages to bootstrap RAG chunk pool
            if not chunks:
                msg_sample_stmt = select(Message).order_by(desc(Message.sent_at)).limit(30)
                sample_msgs = (await self.session.execute(msg_sample_stmt)).scalars().all()
                if sample_msgs:
                    conv_ids = list({m.conversation_id for m in sample_msgs})
                    for cid in conv_ids[:2]:
                        await self.index_message_chunks(cid, force_mock=force_mock)
                    chunks = (await self.session.execute(chunk_stmt)).scalars().all()

            for chk in chunks:
                if chk.embedding:
                    c_vec = unpack_vector(chk.embedding)
                else:
                    c_vec = await self.embed_single(chk.context_text, force_mock=force_mock)
                sim = cosine_similarity(query_vec, c_vec)
                if sim >= min_score:
                    candidates.append({
                        "entity_type": "chunk",
                        "entity_id": chk.id,
                        "title": f"社群原声对话切片 (ID: {chk.id[:8]})",
                        "snippet": chk.context_text[:180],
                        "score": round(sim, 4),
                        "evidence_uri": f"chatinsight://conv/{chk.conversation_id}/msg_{chk.center_message_id}#chunk",
                        "metadata": {
                            "conversation_id": chk.conversation_id,
                            "center_message_id": chk.center_message_id,
                            "full_context": chk.context_text,
                        },
                        "object": chk,
                    })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:limit]

    async def search_semantic_evidence_for_category(
        self,
        category_name: str,
        limit: int = 5,
        force_mock: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Retrieves top RAG-grounded quotes, issues, and context snippets for a business category.
        Used by the VoC reporting engine for executive strategic synthesis.
        """
        query = f"社群关于 {category_name} 模块的核心痛点、高频故障与用户真实体验反馈"
        return await self.search_dense_candidates(
            query=query,
            limit=limit,
            entity_types=("topic", "insight", "chunk"),
            min_score=0.18,
            force_mock=force_mock,
        )

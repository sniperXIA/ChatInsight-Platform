import re
import time
from datetime import datetime
from typing import Optional
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.insights.tag_manager import BUILTIN_TAGS
from packages.model_gateway.gateway import ModelGateway
from packages.model_gateway.settings_manager import SettingsManager
from packages.persistence.models import (
    Conversation,
    Insight,
    MediaAsset,
    MediaEnrichment,
    Message,
    Topic,
)
from packages.retrieval.vector_math import cosine_similarity, reciprocal_rank_fusion
from packages.retrieval.vector_service import VectorService
from packages.search.contracts import (
    HybridSearchResponse,
    SearchFilter,
    SearchResultItem,
)

STOPWORDS = {
    "请问", "有哪些", "是什么", "关于", "如何", "怎么", "社群", "玩家", "反馈",
    "目前", "层面", "用户", "反映", "问题", "主要", "情况", "总结", "分析",
    "相关", "建议", "想了解", "主要有", "哪些", "主要有哪些",
    "大家", "经常", "主要存在", "存在", "主要问题", "大家反映",
    "产品", "方面", "维度", "有哪些问题", "有哪些建议", "有什么", "现在", "目前在",
    "求问", "咨询", "讨论", "有没有", "是否有", "是否有反馈过", "有过", "反馈过",
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好", "自己", "这", "与", "或", "等", "及"
}


class HybridSearchEngine:
    """Hybrid search engine combining lexical keyword matching, token similarity, and dense vector RRF ranking."""

    def __init__(
        self,
        session: AsyncSession,
        vector_service: Optional[VectorService] = None,
        model_gateway: Optional[ModelGateway] = None,
    ):
        self.session = session
        self.gateway = model_gateway or ModelGateway()
        self.vector_service = vector_service or VectorService(session, self.gateway)

    def _detect_query_modules(self, query: str) -> set[str]:
        """Detects matching module categories, keys and names from query using taxonomy tags."""
        q_lower = query.lower()
        matched: set[str] = set()
        for t in BUILTIN_TAGS:
            k = t.get("key", "").lower()
            n = t.get("name_zh", "").lower()
            cat = t.get("category", "").lower()
            # Direct key/name match
            if (k and k in q_lower) or (n and n in q_lower):
                if cat:
                    matched.add(cat)
                matched.add(k)
                matched.add(t.get("name_zh", "").lower())
            # Check domain keywords
            for kw in t.get("keywords", []):
                if len(kw) >= 2 and kw.lower() in q_lower:
                    if cat:
                        matched.add(cat)
                    matched.add(k)
                    matched.add(t.get("name_zh", "").lower())
        return matched

    def _is_module_matched(self, meta_text: str, detected_modules: set[str]) -> bool:
        """Checks if metadata text matches any detected query module/tag."""
        if not detected_modules or not meta_text:
            return False
        meta_lower = meta_text.lower()
        meta_clean = re.sub(r"[^\w\u4e00-\u9fa5]", " ", meta_lower)
        meta_tokens = set(meta_clean.split())
        for m in detected_modules:
            m_lower = m.lower()
            if m_lower in meta_lower or meta_lower in m_lower:
                return True
            m_clean = re.sub(r"[^\w\u4e00-\u9fa5]", " ", m_lower)
            m_tokens = set(m_clean.split())
            if meta_tokens.intersection(m_tokens):
                return True
            for mt in meta_tokens:
                for mod_t in m_tokens:
                    if len(mt) >= 2 and len(mod_t) >= 2:
                        if mt in mod_t or mod_t in mt:
                            return True
        return False

    async def search(
        self,
        query: str,
        filters: SearchFilter = SearchFilter(),
        limit: int = 20,
    ) -> HybridSearchResponse:
        t0 = time.perf_counter()
        clean_q = query.strip()
        tokens = self._tokenize(clean_q)
        content_tokens = {t for t in tokens if t not in STOPWORDS and len(t) >= 2}
        detected_modules = self._detect_query_modules(clean_q)

        # Determine if the query is an exploratory broad category question
        # (e.g. "目前UI层面用户反映的问题主要有哪些？") or a specific keyword query
        non_module_tokens = [
            ct for ct in content_tokens
            if not any(ct in m or m in ct for m in detected_modules)
        ]
        is_pure_category = len(non_module_tokens) == 0 and bool(detected_modules)

        results: list[SearchResultItem] = []

        # 1. Search Topics
        topic_stmt = select(Topic)
        if filters.modules:
            topic_stmt = topic_stmt.where(Topic.module.in_(filters.modules))
        if filters.statuses:
            topic_stmt = topic_stmt.where(Topic.status.in_(filters.statuses))
        if filters.severities:
            topic_stmt = topic_stmt.where(Topic.severity.in_(filters.severities))

        topic_res = await self.session.execute(topic_stmt.limit(50))
        topics = topic_res.scalars().all()

        for t in topics:
            t_mod = (t.module or "").lower()
            t_tags = " ".join(t.tags_json or []).lower()
            t_meta = f"{t_mod} {t_tags} {t.title}"
            mod_matched = self._is_module_matched(t_meta, detected_modules) if detected_modules else False

            base_score = self._compute_relevance(clean_q, content_tokens or tokens, f"{t.title} {t.summary} {t.module}")
            if is_pure_category and mod_matched:
                score = (base_score or 0.15) + 0.60
            elif mod_matched and base_score >= 0.25:
                score = base_score + 0.50
            else:
                score = max(0.0, base_score - (0.20 if (detected_modules and not mod_matched) else 0.0))

            min_threshold = 0.20
            if score >= min_threshold:
                results.append(
                    SearchResultItem(
                        entity_type="topic",
                        entity_id=t.id,
                        title=t.title,
                        snippet=t.summary[:150],
                        score=score + 0.15,  # Boost aggregated topics
                        evidence_uri=f"chatinsight://topics/{t.id}",
                        metadata={
                            "module": t.module,
                            "severity": t.severity,
                            "status": t.status,
                            "feedback_count": t.feedback_count,
                        },
                    )
                )

        # 2. Search Insights
        insight_stmt = select(Insight)
        if filters.modules:
            insight_stmt = insight_stmt.where(Insight.module.in_(filters.modules))
        if filters.insight_types:
            insight_stmt = insight_stmt.where(Insight.insight_type.in_(filters.insight_types))
        if filters.severities:
            insight_stmt = insight_stmt.where(Insight.severity.in_(filters.severities))

        insight_res = await self.session.execute(insight_stmt.limit(50))
        insights = insight_res.scalars().all()

        for ins in insights:
            ins_mod = (ins.module or "").lower()
            ins_tags = " ".join(ins.tags_json or []).lower()
            ins_meta = f"{ins_mod} {ins_tags} {ins.summary}"
            mod_matched = self._is_module_matched(ins_meta, detected_modules) if detected_modules else False

            base_score = self._compute_relevance(clean_q, content_tokens or tokens, f"{ins.summary} {ins.description} {ins.module}")
            if is_pure_category and mod_matched:
                score = (base_score or 0.15) + 0.60
            elif mod_matched and base_score >= 0.25:
                score = base_score + 0.50
            else:
                score = max(0.0, base_score - (0.20 if (detected_modules and not mod_matched) else 0.0))

            min_threshold = 0.20
            if score >= min_threshold:
                results.append(
                    SearchResultItem(
                        entity_type="insight",
                        entity_id=ins.id,
                        title=f"[{ins.insight_type.upper()}] {ins.summary}",
                        snippet=ins.description[:150],
                        score=score + 0.1,
                        evidence_uri=f"chatinsight://insights/{ins.id}",
                        metadata={
                            "module": ins.module,
                            "severity": ins.severity,
                            "status_in_chat": ins.status_in_chat,
                            "support_known": ins.support_known_status,
                        },
                    )
                )

        # 3. Search Messages
        msg_stmt = select(Message)
        if filters.start_time:
            msg_stmt = msg_stmt.where(Message.sent_at >= filters.start_time)
        if filters.end_time:
            msg_stmt = msg_stmt.where(Message.sent_at <= filters.end_time)

        msg_res = await self.session.execute(msg_stmt.limit(100))
        messages = msg_res.scalars().all()

        for msg in messages:
            msg_clean = msg.raw_text.lower()
            score = self._compute_relevance(clean_q, content_tokens or tokens, msg.raw_text)
            # Check if message contains high-value content tokens
            has_domain_token = any(ct in msg_clean for ct in content_tokens) if content_tokens else False
            if has_domain_token:
                score += 0.25
            elif detected_modules:
                score -= 0.35

            min_msg_threshold = 0.30 if detected_modules else 0.20
            if score >= min_msg_threshold:
                results.append(
                    SearchResultItem(
                        entity_type="message",
                        entity_id=msg.id,
                        title=f"群消息 ({msg.sent_at.strftime('%m-%d %H:%M')})",
                        snippet=msg.raw_text[:120],
                        score=score,
                        evidence_uri=f"chatinsight://conv/{msg.conversation_id}/msg_{msg.id}#text",
                        metadata={"conversation_id": msg.conversation_id, "sent_at": msg.sent_at.isoformat()},
                    )
                )

        # 4. Search Media Enrichments (OCR & Visual Summary)
        media_stmt = select(MediaEnrichment, MediaAsset).join(MediaAsset, MediaEnrichment.media_id == MediaAsset.id)
        media_res = await self.session.execute(media_stmt.limit(50))
        for enrich, media in media_res.all():
            score = self._compute_relevance(clean_q, content_tokens or tokens, enrich.searchable_text)
            if score > 0.1 or any(ct in enrich.searchable_text.lower() for ct in content_tokens):
                results.append(
                    SearchResultItem(
                        entity_type="media",
                        entity_id=media.id,
                        title=f"多模态媒体 [{media.kind.upper()}]",
                        snippet=enrich.summary[:150] if enrich.summary else "",
                        score=score,
                        evidence_uri=f"chatinsight://media/{media.id}#enrichment",
                        metadata={"kind": media.kind, "media_id": media.id},
                    )
                )

        # RRF Dense Vector Re-ranking
        settings = SettingsManager.get_settings()
        embedding_cfg = getattr(settings, "embedding", None)
        vector_enabled = embedding_cfg.enabled if embedding_cfg else True

        if vector_enabled and results and self.vector_service:
            try:
                query_vec = await self.vector_service.embed_single(clean_q)
                dense_ranked: list[tuple[SearchResultItem, float]] = []

                for item in results:
                    dense_sim = 0.0
                    if item.entity_type == "topic":
                        topic_obj = next((t for t in topics if t.id == item.entity_id), None)
                        if topic_obj:
                            t_vec = await self.vector_service.get_or_create_topic_vector(topic_obj)
                            dense_sim = cosine_similarity(query_vec, t_vec)
                    elif item.entity_type == "insight":
                        ins_obj = next((ins for ins in insights if ins.id == item.entity_id), None)
                        if ins_obj:
                            ins_vec = await self.vector_service.get_insight_vector(ins_obj)
                            dense_sim = cosine_similarity(query_vec, ins_vec)
                    elif item.entity_type == "message":
                        msg_vec = await self.vector_service.embed_single(item.snippet)
                        dense_sim = cosine_similarity(query_vec, msg_vec)

                    if dense_sim > 0:
                        dense_ranked.append((item, dense_sim))

                if dense_ranked:
                    dense_ranked.sort(key=lambda x: x[1], reverse=True)
                    sparse_ranked = [(item, item.score) for item in results]
                    fused = reciprocal_rank_fusion(
                        sparse_items=sparse_ranked,
                        dense_items=dense_ranked,
                        k=60,
                        weight_sparse=1.0,
                        weight_dense=1.2,
                    )
                    results = []
                    for itm, fscore in fused:
                        itm.score = round(fscore * 60.0, 3)
                        results.append(itm)
            except Exception:
                pass

        # Sort descending by relevance score
        results.sort(key=lambda x: x.score, reverse=True)
        final_results = results[:limit]

        duration = (time.perf_counter() - t0) * 1000
        return HybridSearchResponse(
            query=clean_q,
            total_hits=len(results),
            results=final_results,
            duration_ms=round(duration, 2),
        )

    def _tokenize(self, text: str) -> set[str]:
        cleaned = re.sub(r"[^\w\u4e00-\u9fa5]", " ", text.lower())
        tokens = set()
        en_words = re.findall(r"[a-z0-9_]+", cleaned)
        for w in en_words:
            if len(w) >= 2 and w not in STOPWORDS:
                tokens.add(w)

        zh_clean = cleaned
        for sw in sorted(STOPWORDS, key=len, reverse=True):
            zh_clean = re.sub(r"\b" + re.escape(sw) + r"\b" if sw.isascii() else re.escape(sw), " ", zh_clean)

        zh_chunks = [c.strip() for c in re.findall(r"[\u4e00-\u9fa5]+", zh_clean) if c.strip()]
        for chunk in zh_chunks:
            if chunk in STOPWORDS:
                continue
            if len(chunk) == 1:
                tokens.add(chunk)
            elif len(chunk) <= 4:
                tokens.add(chunk)
                for i in range(len(chunk) - 1):
                    tokens.add(chunk[i : i + 2])
            else:
                tokens.add(chunk)
                for i in range(len(chunk) - 1):
                    tokens.add(chunk[i : i + 2])
                for i in range(len(chunk) - 2):
                    tokens.add(chunk[i : i + 3])
        return {t for t in tokens if t not in STOPWORDS}

    def _compute_relevance(self, query: str, query_tokens: set[str], target_text: str) -> float:
        if not target_text:
            return 0.0
        target_clean = target_text.lower()
        if query.lower() in target_clean:
            return 0.85
        target_tokens = self._tokenize(target_clean)
        if not query_tokens or not target_tokens:
            return 0.0
        inter = len(query_tokens.intersection(target_tokens))
        return round(inter / len(query_tokens), 2)


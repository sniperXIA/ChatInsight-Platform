import re
from typing import Any, Mapping, Optional, Sequence
from packages.persistence.models import Insight, Topic
from packages.retrieval.vector_math import cosine_similarity


class CandidateRanker:
    """
    Computes lexical and dense vector semantic candidate recall scores
    between an Insight and existing Topics for two-stage clustering.
    """

    def __init__(self, vector_service: Any = None):
        self.vector_service = vector_service

    async def rank_candidate_topics_async(
        self,
        insight: Insight,
        candidate_topics: Sequence[Topic],
        min_threshold: float = 0.2,
        force_mock: bool = False,
    ) -> list[tuple[Topic, float]]:
        """
        Asynchronously ranks candidate topics using dense embedding vectors when available,
        falling back to Jaccard lexical similarity.
        """
        if not candidate_topics:
            return []

        if self.vector_service:
            try:
                insight_vec = await self.vector_service.get_insight_vector(insight, force_mock=force_mock)
                topic_vectors: dict[str, list[float]] = {}
                for t in candidate_topics:
                    topic_vectors[t.id] = await self.vector_service.get_or_create_topic_vector(t, force_mock=force_mock)

                return self.rank_candidate_topics(
                    insight=insight,
                    candidate_topics=candidate_topics,
                    min_threshold=min_threshold,
                    insight_vector=insight_vec,
                    topic_vectors=topic_vectors,
                )
            except Exception:
                # Graceful fallback to lexical ranking
                pass

        return self.rank_candidate_topics(
            insight=insight,
            candidate_topics=candidate_topics,
            min_threshold=min_threshold,
        )

    def rank_candidate_topics(
        self,
        insight: Insight,
        candidate_topics: Sequence[Topic],
        min_threshold: float = 0.2,
        insight_vector: Optional[Sequence[float]] = None,
        topic_vectors: Optional[Mapping[str, Sequence[float]]] = None,
    ) -> list[tuple[Topic, float]]:
        """
        Synchronously ranks candidate topics.
        If insight_vector and topic_vectors are provided, uses dense cosine similarity.
        Otherwise, uses N-gram Jaccard lexical similarity.
        """
        if not candidate_topics:
            return []

        insight_tokens = self._tokenize(f"{insight.module} {insight.summary} {insight.description}")
        scored: list[tuple[Topic, float]] = []

        for topic in candidate_topics:
            module_match = 0.25 if (topic.module == insight.module) else 0.0

            # 1. Dense Semantic Similarity
            if insight_vector and topic_vectors and topic.id in topic_vectors:
                sem_sim = cosine_similarity(insight_vector, topic_vectors[topic.id])
                # Combine module prior with semantic similarity
                total_score = round(module_match + (0.75 * sem_sim), 3)
            else:
                # 2. Lexical Fallback
                topic_tokens = self._tokenize(f"{topic.module} {topic.title} {topic.summary}")
                lexical_sim = self._jaccard_similarity(insight_tokens, topic_tokens)
                total_score = round(module_match + (0.75 * lexical_sim), 3)

            if total_score >= min_threshold:
                scored.append((topic, total_score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _tokenize(self, text: str) -> set[str]:
        cleaned = re.sub(r"[^\w\u4e00-\u9fa5]", " ", text.lower())
        words = cleaned.split()
        tokens = set(words)
        for w in words:
            for i in range(len(w) - 1):
                tokens.add(w[i : i + 2])
        return tokens

    def _jaccard_similarity(self, s1: set[str], s2: set[str]) -> float:
        if not s1 or not s2:
            return 0.0
        intersection = len(s1.intersection(s2))
        union = len(s1.union(s2))
        return intersection / union if union > 0 else 0.0

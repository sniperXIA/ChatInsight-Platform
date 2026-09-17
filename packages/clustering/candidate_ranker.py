import re
from typing import Sequence
from packages.persistence.models import Insight, Topic


class CandidateRanker:
    """Computes lexical and semantic candidate recall scores between an Insight and existing Topics."""

    def rank_candidate_topics(
        self,
        insight: Insight,
        candidate_topics: Sequence[Topic],
        min_threshold: float = 0.2,
    ) -> list[tuple[Topic, float]]:
        if not candidate_topics:
            return []

        insight_tokens = self._tokenize(f"{insight.module} {insight.summary} {insight.description}")
        scored: list[tuple[Topic, float]] = []

        for topic in candidate_topics:
            # High base score if exact module matches
            module_match = 0.3 if (topic.module == insight.module) else 0.0
            
            topic_tokens = self._tokenize(f"{topic.module} {topic.title} {topic.summary}")
            lexical_sim = self._jaccard_similarity(insight_tokens, topic_tokens)
            
            total_score = round(module_match + (0.7 * lexical_sim), 3)
            if total_score >= min_threshold:
                scored.append((topic, total_score))

        # Sort descending by score
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _tokenize(self, text: str) -> set[str]:
        cleaned = re.sub(r"[^\w\u4e00-\u9fa5]", " ", text.lower())
        # Character 2-grams for Chinese + whitespace split for English
        words = cleaned.split()
        tokens = set(words)
        # Add Chinese 2-grams
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

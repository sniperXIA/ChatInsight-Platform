from datetime import datetime
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from packages.clustering.candidate_ranker import CandidateRanker
from packages.clustering.contracts import JudgeDecision, PairwiseJudgeOutput
from packages.clustering.pairwise_judge import PairwiseJudge
from packages.model_gateway.gateway import ModelGateway
from packages.persistence.models import Insight, Topic
from packages.persistence.repositories.registry import RepositoryRegistry
from packages.retrieval.vector_service import VectorService


class ClusterManager:
    """Orchestrates two-stage clustering: candidate ranking via dense vector / lexical similarity followed by pairwise LLM adjudication."""

    def __init__(self, session: AsyncSession, model_gateway: ModelGateway | None = None):
        self.session = session
        self.gateway = model_gateway or ModelGateway()
        self.vector_service = VectorService(session, self.gateway)
        self.ranker = CandidateRanker(vector_service=self.vector_service)
        self.judge = PairwiseJudge(session, self.gateway)
        self.repo = RepositoryRegistry(session)

    async def cluster_insight(
        self,
        insight_id: str,
        force_mock: bool = False,
        model_override: str | None = None,
    ) -> tuple[Topic, JudgeDecision, Optional[PairwiseJudgeOutput]]:
        insight = await self.repo.get_insight_by_id(insight_id)
        if not insight:
            raise FileNotFoundError(f"Insight {insight_id} not found")

        # 1. Candidate Recall Stage (Dense Semantic Vector + Lexical Fallback)
        active_topics = await self.repo.get_active_topics_by_module(insight.module)
        ranked_candidates = await self.ranker.rank_candidate_topics_async(
            insight=insight,
            candidate_topics=active_topics,
            min_threshold=0.25,
            force_mock=force_mock,
        )

        # 2. Pairwise Judge Stage (evaluate top candidate)
        if ranked_candidates:
            top_topic, score = ranked_candidates[0]
            judge_res = await self.judge.judge_pair(
                topic=top_topic,
                insight=insight,
                force_mock=force_mock,
                model_override=model_override,
            )

            if judge_res.decision == JudgeDecision.SAME_ISSUE:
                # Merge into existing Topic
                await self.repo.update_topic_stats(
                    topic_id=top_topic.id,
                    increment_feedback=1,
                    increment_users=1,
                    new_seen_time=insight.created_at,
                    merged_title=judge_res.merged_title_suggestion,
                    merged_summary=judge_res.merged_summary_suggestion,
                )
                await self.repo.link_insight_to_topic(
                    topic_id=top_topic.id,
                    insight_id=insight.id,
                    relation_type="instance",
                )
                await self._emit_cluster_event(top_topic, insight, "merged")
                return top_topic, JudgeDecision.SAME_ISSUE, judge_res

            elif judge_res.decision == JudgeDecision.SUB_ISSUE:
                await self.repo.update_topic_stats(
                    topic_id=top_topic.id,
                    increment_feedback=1,
                    increment_users=1,
                    new_seen_time=insight.created_at,
                )
                await self.repo.link_insight_to_topic(
                    topic_id=top_topic.id,
                    insight_id=insight.id,
                    relation_type="sub_issue",
                )
                await self._emit_cluster_event(top_topic, insight, "sub_issue_linked")
                return top_topic, JudgeDecision.SUB_ISSUE, judge_res

        # 3. Create New Topic
        new_topic = await self.repo.create_topic(
            workspace_id=insight.workspace_id,
            title=insight.summary,
            summary=insight.description,
            module=insight.module,
            sub_module=insight.sub_module,
            severity=insight.severity,
            tags=insight.tags_json or [],
            first_seen_at=insight.created_at,
        )
        await self.repo.link_insight_to_topic(
            topic_id=new_topic.id,
            insight_id=insight.id,
            relation_type="instance",
        )
        await self._emit_cluster_event(new_topic, insight, "created")
        return new_topic, JudgeDecision.DISTINCT_ISSUE, None

    async def _emit_cluster_event(self, topic: Topic, insight: Insight, action: str):
        idempotency_key = f"cluster_{topic.id}_{insight.id}_{action}"
        await self.repo.record_outbox_event(
            event_type="topic.feedback_aggregated",
            aggregate_type="topic",
            aggregate_id=topic.id,
            payload={
                "topic_id": topic.id,
                "insight_id": insight.id,
                "action": action,
                "feedback_count": topic.feedback_count,
                "timestamp": datetime.now().isoformat(),
            },
            idempotency_key=idempotency_key,
        )

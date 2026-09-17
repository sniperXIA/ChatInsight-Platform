from typing import Any, Optional
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from packages.persistence.models import (
    AnalysisRun,
    Conversation,
    Episode,
    EpisodeMessage,
    ImportBatch,
    Insight,
    InsightClaim,
    InsightPushRecord,
    MediaAsset,
    MediaEnrichment,
    Message,
    MessageMediaLink,
    Participant,
    ParticipantAlias,
    SourceFile,
    SourceRoot,
    TaskExecution,
    Topic,
    TopicInsightLink,
)


class DataPurgerService:
    """
    Dedicated service for granularly purging business data and execution records
    associated with specific pipeline steps or selected task execution items.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def purge(
        self,
        steps: list[str],
        task_ids: Optional[list[str]] = None,
        clear_all_tasks: bool = False,
        clear_tasks_for_selected_steps: bool = True,
    ) -> dict[str, int]:
        """
        Executes selective clearing of step data and task execution logs.
        Returns a summary dictionary with counts of records cleared per entity.
        """
        counts: dict[str, int] = {
            "messages": 0,
            "media_assets": 0,
            "media_enrichments": 0,
            "conversations": 0,
            "participants": 0,
            "source_files": 0,
            "import_batches": 0,
            "episodes": 0,
            "episode_messages": 0,
            "insights": 0,
            "insight_claims": 0,
            "topics": 0,
            "topic_insight_links": 0,
            "analysis_runs": 0,
            "task_executions": 0,
        }

        normalized_steps = set(steps or [])

        # 1. Step 1: Scan & Import (全量原始聊天与文件)
        if "scan_import" in normalized_steps or "import" in normalized_steps:
            # Downstream items depend on messages/conversations, so clean everything downstream too
            res_til = await self.session.execute(delete(TopicInsightLink))
            counts["topic_insight_links"] += res_til.rowcount or 0

            res_top = await self.session.execute(delete(Topic))
            counts["topics"] += res_top.rowcount or 0

            res_ic = await self.session.execute(delete(InsightClaim))
            counts["insight_claims"] += res_ic.rowcount or 0

            res_ins = await self.session.execute(delete(Insight))
            counts["insights"] += res_ins.rowcount or 0

            res_em = await self.session.execute(delete(EpisodeMessage))
            counts["episode_messages"] += res_em.rowcount or 0

            res_ep = await self.session.execute(delete(Episode))
            counts["episodes"] += res_ep.rowcount or 0

            res_me = await self.session.execute(delete(MediaEnrichment))
            counts["media_enrichments"] += res_me.rowcount or 0

            res_mml = await self.session.execute(delete(MessageMediaLink))
            res_ma = await self.session.execute(delete(MediaAsset))
            counts["media_assets"] += res_ma.rowcount or 0

            res_msg = await self.session.execute(delete(Message))
            counts["messages"] += res_msg.rowcount or 0

            res_pa = await self.session.execute(delete(ParticipantAlias))
            res_part = await self.session.execute(delete(Participant))
            counts["participants"] += res_part.rowcount or 0

            res_batch = await self.session.execute(delete(ImportBatch))
            counts["import_batches"] += res_batch.rowcount or 0

            res_sf = await self.session.execute(delete(SourceFile))
            counts["source_files"] += res_sf.rowcount or 0

            res_conv = await self.session.execute(delete(Conversation))
            counts["conversations"] += res_conv.rowcount or 0

            res_ar = await self.session.execute(delete(AnalysisRun))
            counts["analysis_runs"] += res_ar.rowcount or 0

        else:
            # Handle individual downstream steps if scan_import was NOT selected

            # 2. Step 2: Multimodal Enrichment
            if "multimodal" in normalized_steps:
                res_me = await self.session.execute(delete(MediaEnrichment))
                counts["media_enrichments"] += res_me.rowcount or 0

                # Reset metadata on media assets
                await self.session.execute(
                    update(MediaAsset).values(
                        derived_preview_relpath=None,
                        metadata_json={},
                    )
                )

                res_ar = await self.session.execute(
                    delete(AnalysisRun).where(
                        AnalysisRun.run_type.in_(["image_enrichment", "video_enrichment"])
                    )
                )
                counts["analysis_runs"] += res_ar.rowcount or 0

            # 3. Step 3: Episode Segmentation
            if "segmentation" in normalized_steps:
                # Cleaning episodes also clears downstream insights & topic links
                res_til = await self.session.execute(delete(TopicInsightLink))
                counts["topic_insight_links"] += res_til.rowcount or 0

                res_top = await self.session.execute(delete(Topic))
                counts["topics"] += res_top.rowcount or 0

                res_ic = await self.session.execute(delete(InsightClaim))
                counts["insight_claims"] += res_ic.rowcount or 0

                res_ins = await self.session.execute(delete(Insight))
                counts["insights"] += res_ins.rowcount or 0

                res_em = await self.session.execute(delete(EpisodeMessage))
                counts["episode_messages"] += res_em.rowcount or 0

                res_ep = await self.session.execute(delete(Episode))
                counts["episodes"] += res_ep.rowcount or 0

                res_ar = await self.session.execute(
                    delete(AnalysisRun).where(
                        AnalysisRun.run_type.in_(["episode", "insight_extraction", "insight", "pairwise_judge"])
                    )
                )
                counts["analysis_runs"] += res_ar.rowcount or 0

            # 4. Step 4: Insight Extraction
            if "insights" in normalized_steps or "insight_extraction" in normalized_steps:
                res_til = await self.session.execute(delete(TopicInsightLink))
                counts["topic_insight_links"] += res_til.rowcount or 0

                res_top = await self.session.execute(delete(Topic))
                counts["topics"] += res_top.rowcount or 0

                res_ic = await self.session.execute(delete(InsightClaim))
                counts["insight_claims"] += res_ic.rowcount or 0

                res_ins = await self.session.execute(delete(Insight))
                counts["insights"] += res_ins.rowcount or 0

                res_ar = await self.session.execute(
                    delete(AnalysisRun).where(
                        AnalysisRun.run_type.in_(["insight_extraction", "insight", "pairwise_judge"])
                    )
                )
                counts["analysis_runs"] += res_ar.rowcount or 0

            # 5. Step 5: Topic Clustering
            if "clustering" in normalized_steps or "topic_clustering" in normalized_steps:
                res_til = await self.session.execute(delete(TopicInsightLink))
                counts["topic_insight_links"] += res_til.rowcount or 0

                res_top = await self.session.execute(delete(Topic))
                counts["topics"] += res_top.rowcount or 0

                res_ar = await self.session.execute(
                    delete(AnalysisRun).where(AnalysisRun.run_type.in_(["pairwise_judge"]))
                )
                counts["analysis_runs"] += res_ar.rowcount or 0

            # 6. Step 6: Feishu Bitable Push (or legacy ABC Sync)
            if "abc_sync" in normalized_steps or "bitable_push" in normalized_steps:
                res_push = await self.session.execute(delete(InsightPushRecord))
                counts["insight_push_records"] = res_push.rowcount or 0
                await self.session.execute(
                    update(Topic).values(
                        abc_sync_state="not_synced",
                        abc_external_id=None,
                    )
                )

        # 7. Task Execution History Purge
        task_types_to_clear = set()
        for step in normalized_steps:
            if step in ("scan_import", "import"):
                task_types_to_clear.update(["scan_import", "import"])
            elif step == "multimodal":
                task_types_to_clear.add("multimodal")
            elif step == "segmentation":
                task_types_to_clear.add("segmentation")
            elif step in ("insights", "insight_extraction"):
                task_types_to_clear.update(["insights", "insight_extraction"])
            elif step in ("clustering", "topic_clustering"):
                task_types_to_clear.update(["clustering", "topic_clustering"])
            elif step in ("abc_sync", "bitable_push"):
                task_types_to_clear.update(["abc_sync", "bitable_push"])
            elif step == "full_pipeline":
                task_types_to_clear.add("full_pipeline")

        # Check if full pipeline should be cleared when all or most steps are cleared
        if "full_pipeline" in normalized_steps or (
            "scan_import" in normalized_steps and "segmentation" in normalized_steps
        ):
            task_types_to_clear.add("full_pipeline")

        if clear_all_tasks:
            # Clear all non-running tasks
            t_stmt = delete(TaskExecution).where(TaskExecution.status != "running")
            t_res = await self.session.execute(t_stmt)
            counts["task_executions"] += t_res.rowcount or 0
        else:
            if task_ids:
                t_stmt = delete(TaskExecution).where(
                    TaskExecution.id.in_(task_ids),
                    TaskExecution.status != "running",
                )
                t_res = await self.session.execute(t_stmt)
                counts["task_executions"] += t_res.rowcount or 0

            if clear_tasks_for_selected_steps and task_types_to_clear:
                t_stmt = delete(TaskExecution).where(
                    TaskExecution.task_type.in_(list(task_types_to_clear)),
                    TaskExecution.status != "running",
                )
                t_res = await self.session.execute(t_stmt)
                counts["task_executions"] += t_res.rowcount or 0

        await self.session.commit()
        return counts

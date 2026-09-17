from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession

from packages.clustering.contracts import JudgeDecision, PairwiseJudgeOutput
from packages.model_gateway.gateway import ModelGateway
from packages.observability.analysis_service import AnalysisService
from packages.persistence.models import Insight, Topic


class PairwiseJudge:
    """Uses LLM to perform nuanced pairwise comparison to determine if an Insight merges with a Topic."""

    def __init__(self, session: AsyncSession, model_gateway: ModelGateway | None = None):
        self.session = session
        self.gateway = model_gateway or ModelGateway()
        self.analysis_service = AnalysisService(session)

    async def judge_pair(
        self,
        topic: Topic,
        insight: Insight,
        force_mock: bool = False,
        model_override: str | None = None,
    ) -> PairwiseJudgeOutput:
        from packages.model_gateway.settings_manager import SettingsManager
        settings = SettingsManager.get_settings()
        mod_cfg = settings.clustering_judge

        provider = self.gateway.get_text_provider(force_mock=force_mock)
        provider_name = "mock" if force_mock else (settings.provider or "openrouter")
        selected_model = model_override or mod_cfg.model or getattr(provider, "default_text_model", "deepseek/deepseek-chat")

        system_prompt = mod_cfg.system_prompt

        user_prompt = (
            f"【已有产品主题 (Existing Topic)】\n"
            f"- 模块: {topic.module} / {topic.sub_module or '通用'}\n"
            f"- 标题: {topic.title}\n"
            f"- 摘要: {topic.summary}\n"
            f"- 历史反馈频次: {topic.feedback_count}\n\n"
            f"【新增用户洞察 (New Insight)】\n"
            f"- 模块: {insight.module} / {insight.sub_module or '通用'}\n"
            f"- 类型: {insight.insight_type}\n"
            f"- 摘要: {insight.summary}\n"
            f"- 描述: {insight.description}\n\n"
            "请判定两者的归属关系 (SAME_ISSUE | SUB_ISSUE | DISTINCT_ISSUE)。"
        )

        async def _call_judge() -> tuple[PairwiseJudgeOutput, dict[str, Any]]:
            if force_mock:
                # Deterministic check: if summary keywords overlap strongly -> SAME_ISSUE
                is_same = (topic.module == insight.module and ("音色" in topic.title and "音色" in insight.summary))
                decision = JudgeDecision.SAME_ISSUE if is_same else JudgeDecision.DISTINCT_ISSUE
                mock_out = PairwiseJudgeOutput(
                    decision=decision,
                    confidence=0.95,
                    rationale=f"根据模块与核心词分析，两者均讨论 {topic.module} 相关问题，判定为 {decision.value}",
                    merged_title_suggestion=topic.title if is_same else None,
                    merged_summary_suggestion=f"{topic.summary}；同时有用户新增反馈：{insight.summary}" if is_same else None,
                )
                return mock_out, {"prompt_tokens": 350, "completion_tokens": 100, "total_tokens": 450}

            return await provider.generate_structured(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_schema=PairwiseJudgeOutput,
                temperature=mod_cfg.temperature,
                top_p=mod_cfg.top_p,
                top_k=mod_cfg.top_k,
                max_tokens=mod_cfg.max_tokens,
                model=selected_model,
                reasoning_effort=getattr(mod_cfg, "reasoning_effort", "none"),
                enable_thinking=getattr(mod_cfg, "enable_thinking", None),
                thinking_budget=getattr(mod_cfg, "thinking_budget", None),
                preserve_thinking=getattr(mod_cfg, "preserve_thinking", None),
                max_completion_tokens=getattr(mod_cfg, "max_completion_tokens", None),
            )

        output, run, _ = await self.analysis_service.execute_cached(
            workspace_id=insight.workspace_id,
            run_type="pairwise_judge",
            target_type="insight",
            target_id=insight.id,
            provider=provider_name,
            model=selected_model,
            prompt_key="pairwise_judge_v2",
            input_data={"topic_id": topic.id, "insight_id": insight.id},
            config_data={
                "model": selected_model,
                "schema": "PairwiseJudgeOutput_v2",
                "temperature": mod_cfg.temperature,
                "top_p": mod_cfg.top_p,
            },
            response_schema=PairwiseJudgeOutput,
            call_fn=_call_judge,
        )

        return output

from datetime import datetime
from enum import StrEnum
from typing import Any, Optional
from pydantic import BaseModel, Field


class JudgeDecision(StrEnum):
    SAME_ISSUE = "SAME_ISSUE"  # 同一问题，直接合并
    SUB_ISSUE = "SUB_ISSUE"    # 关联子问题/不同表现形式
    DISTINCT_ISSUE = "DISTINCT_ISSUE"  # 独立不同问题


class PairwiseJudgeOutput(BaseModel):
    decision: JudgeDecision = Field(description="SAME_ISSUE | SUB_ISSUE | DISTINCT_ISSUE")
    confidence: float = Field(default=0.9, ge=0.0, le=1.0, description="判定置信度")
    rationale: str = Field(description="判定理由与共性/差异点对比分析")
    merged_title_suggestion: Optional[str] = Field(default=None, description="若合并时建议使用的更具概括性的主题标题")
    merged_summary_suggestion: Optional[str] = Field(default=None, description="若合并时建议更新的主题摘要")


class TopicView(BaseModel):
    id: str
    title: str
    summary: str
    module: str
    sub_module: Optional[str] = None
    severity: str
    status: str
    feedback_count: int
    unique_users_count: int
    first_seen_at: str
    last_seen_at: str
    tags: list[str] = Field(default_factory=list)
    abc_sync_state: str = "not_synced"
    abc_external_id: Optional[str] = None
    linked_insights_count: int = 0

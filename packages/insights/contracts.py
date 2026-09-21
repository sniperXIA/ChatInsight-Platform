from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field

from packages.domain.enums import FactualState, InsightType, SeverityLevel


class ClaimItem(BaseModel):
    claim_id: str = Field(description="原子主张唯一ID (如 c_1)")
    claim_text: str = Field(description="可供事实核验的单一陈述句")
    fact_category: str = Field(default="symptom", description="symptom(现象) | trigger(复现动作) | environment(设备/系统环境) | support_response(客服回应)")
    evidence_uris: list[str] = Field(default_factory=list, description="支持该主张的证据URI列表，如 chatinsight://conv/msg_123#text, chatinsight://media_456/ocr")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="置信度打分")
    verification_state: str = Field(default="supported", description="supported | partially_supported | unverified | refuted")


class InsightDraft(BaseModel):
    insight_type: InsightType = Field(description="issue(问题/缺陷) | feature_request(需求/建议) | inquiry(咨询/疑问) | praise(好评)")
    module: str = Field(description="所属产品一级模块（如 硬件/按键, 音色/扩展卡, 伴奏/曲谱, App/蓝牙连接, 固件/升级, 售后/物流）")
    sub_module: Optional[str] = Field(default=None, description="二级细分模块")
    severity: SeverityLevel = Field(default=SeverityLevel.MINOR, description="严重程度 blocker | major | minor | trivial")
    summary: str = Field(description="一句话客观概述核心反馈（不超过40字）")
    description: str = Field(description="详细背景、发生场景与影响面描述")
    status_in_chat: str = Field(default="unresolved", description="群聊中所体现的状态: unresolved(未解决) | support_acknowledged(客服已确认/已知问题) | workaround_provided(已提供规避方法) | fix_confirmed(已确认修复)")
    support_known_status: bool = Field(default=False, description="是否已被官方客服/技术支持明确标记为已知缺陷/已知待办")
    device_model: Optional[str] = Field(default=None, description="针对的硬件设备机型，如 C2, U1 等，若未明确或非硬件问题则为空")
    claims: list[ClaimItem] = Field(default_factory=list, description="构成该洞察的可验证原子主张列表")
    suggested_tags: list[str] = Field(default_factory=list, description="建议打上的业务标签")
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class FactualCheckResult(BaseModel):
    overall_factual_score: float = Field(description="事实一致性得分 0.0~1.0")
    is_fully_supported: bool = Field(description="所有核心 Claim 是否均有确凿证据支持")
    unsupported_claims: list[str] = Field(default_factory=list, description="缺乏证据支持或存在推测的主张ID")
    hallucination_risks: list[str] = Field(default_factory=list, description="潜在幻觉或越界推测风险说明")
    action_recommendation: str = Field(default="auto_approve", description="auto_approve | needs_human_review | reject")


class EpisodeContextItem(BaseModel):
    message_id: str
    sequence: int
    sent_at: str
    sender_label: str
    sender_role: str
    raw_text: str
    quote_text: Optional[str] = None
    media_summaries: list[str] = Field(default_factory=list)
    media_ocr_texts: list[str] = Field(default_factory=list)
    media_uris: list[str] = Field(default_factory=list)


class EpisodeContextPacket(BaseModel):
    episode_id: str
    conversation_id: str
    conversation_name: str
    title: str
    topic_summary: str
    category_hint: str
    started_at: str
    ended_at: str
    messages: list[EpisodeContextItem]

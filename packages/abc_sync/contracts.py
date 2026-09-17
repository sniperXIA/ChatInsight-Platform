from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


class ABCFeedbackPayload(BaseModel):
    external_id: str = Field(description="ChatInsight 平台内部 Topic ID")
    channel_id: str = Field(default="wechat_community", description="反馈来源渠道标识")
    title: str = Field(description="反馈简要标题")
    content: str = Field(description="反馈详细描述与背景")
    category: str = Field(description="产品模块分类")
    severity: str = Field(default="minor", description="严重级别 blocker | major | minor | trivial")
    status: str = Field(default="open", description="ABC 系统中初始状态")
    tags: list[str] = Field(default_factory=list, description="标签列表")
    feedback_count: int = Field(default=1, description="聚合的用户反馈频次")
    unique_users_count: int = Field(default=1, description="影响独立用户数")
    evidence_urls: list[str] = Field(default_factory=list, description="证据链链接")
    reported_at: str = Field(description="首次反馈时间 ISO8601")
    last_updated_at: str = Field(description="最近反馈时间 ISO8601")
    metadata: dict[str, Any] = Field(default_factory=dict, description="其他扩展上下文元数据")


class ABCSyncResult(BaseModel):
    success: bool
    synced_count: int
    batch_id: str
    topic_ids: list[str]
    errors: list[str] = Field(default_factory=list)
    timestamp: str

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field


class FeishuBitableConfig(BaseModel):
    webhook_url: str = Field(default="", description="飞书多维表格 Webhook 地址")
    enable_token_auth: bool = Field(default=False, description="是否启用凭证校验 (Bearer token)")
    bearer_token: str = Field(default="", description="飞书 Webhook 凭证校验 Token")
    enabled: bool = Field(default=True, description="是否启用自动/快捷推送")
    last_tested_at: Optional[str] = None
    last_test_status: Optional[str] = None


class BitableRecordPayload(BaseModel):
    """
    Feishu Bitable exact 6 business fields:
    - 功能模块 (Text)
    - 内容标签 (Multi-select)
    - 反馈类型 (Text)
    - 洞察标题 (Text)
    - 5W1H事实 (Text)
    - 严重级别 (Text)
    """
    module: str = Field(alias="功能模块")
    tags: list[str] = Field(alias="内容标签")
    feedback_type: str = Field(alias="反馈类型")
    title: str = Field(alias="洞察标题")
    fact_5w1h: str = Field(alias="5W1H事实")
    severity: str = Field(alias="严重级别")
    device_model: Optional[str] = Field(default=None, alias="设备机型")

    model_config = ConfigDict(populate_by_name=True)


class PushHistoryRecordItem(BaseModel):
    id: str
    insight_id: str
    target_platform: str = "feishu_bitable"
    webhook_url: str = ""
    state: str = "success"  # success | failed
    status_code: Optional[int] = None
    error_message: Optional[str] = None
    operator: str = "user"
    created_at: str


class PushedInsightItem(BaseModel):
    id: str = ""
    insight_id: str
    title: str
    summary: str = ""
    description: str = ""
    device_model: str = ""
    module: str
    module_zh: str
    tags: list[str] = Field(default_factory=list)
    insight_type: str
    type_zh: str
    type_label_zh: str = ""
    severity: str
    severity_zh: str
    severity_label_zh: str = ""
    priority: str = "P1"
    fact_5w1h: str
    facts_zh: str = ""
    confidence_level: str = "high"
    confidence_reason: str = ""
    factual_score: float = 1.0
    confidence: float = 1.0
    status_in_chat: str = "unresolved"
    status_label_zh: str = "讨论中/未解决"
    last_pushed_at: Optional[str] = None
    last_operator: Optional[str] = None
    push_status: str = "not_pushed"  # success | failed | pending | not_pushed
    push_count: int = 0
    push_history: list[PushHistoryRecordItem] = Field(default_factory=list)
    drilldown_uri: str = ""
    created_at: str


class BitablePushStats(BaseModel):
    total_pushed_insights: int = 0  # 选定时间区间内涉及的去重需求条数
    total_push_attempts: int = 0    # 选定时间区间内累计推送次数
    success_count: int = 0          # 推送成功数
    failed_count: int = 0           # 推送失败数
    success_rate: float = 0.0       # 成功率百分比 (0.0 ~ 100.0)


class BitablePushResponse(BaseModel):
    success: bool
    insight_id: str
    record_id: Optional[str] = None
    status_code: int = 200
    message: str = ""
    error: Optional[str] = None


class BatchPushResponse(BaseModel):
    success: bool
    total: int = 0
    total_attempted: int = 0
    success_count: int = 0
    failed_count: int = 0
    results: list[BitablePushResponse] = Field(default_factory=list)

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


class MetricDistribution(BaseModel):
    category: str
    count: int
    percentage: float


class TopicMetricItem(BaseModel):
    id: str
    title: str
    module: str
    severity: str
    status: str
    feedback_count: int
    unique_users_count: int


class DailyTrendPoint(BaseModel):
    date: str
    message_count: int = 0
    episode_count: int = 0
    insight_count: int = 0
    token_count: int = 0
    runs_count: int = 0


class HourlyTrendPoint(BaseModel):
    hour: str  # "00:00", "01:00", ..., "23:00"
    tokens: int = 0
    runs_count: int = 0


class TokenTrendPoint(BaseModel):
    date: str  # "2026-09-18"
    tokens: int = 0
    runs_count: int = 0


class ModelTokenUsage(BaseModel):
    model: str = ""
    model_name: str = ""
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    runs_count: int = 0
    call_count: int = 0
    percentage: float = 0.0

    def __init__(self, **data: Any):
        if "model" in data and "model_name" not in data:
            data["model_name"] = data["model"]
        elif "model_name" in data and "model" not in data:
            data["model"] = data["model_name"]
        if "runs_count" in data and "call_count" not in data:
            data["call_count"] = data["runs_count"]
        elif "call_count" in data and "runs_count" not in data:
            data["runs_count"] = data["call_count"]
        super().__init__(**data)


class RunTypeMetricItem(BaseModel):
    run_type: str
    name_zh: str
    count: int = 0
    total_tokens: int = 0
    percentage: float = 0.0
    icon: str = "fa-solid fa-layer-group"


class TokenScopeMetrics(BaseModel):
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    analysis_runs_count: int = 0
    models_breakdown: list[ModelTokenUsage] = Field(default_factory=list, description="按大模型名称细分统计的消耗数据")
    runs_by_type: dict[str, int] = Field(default_factory=dict, description="按底层任务类型调用次数统计")
    runs_by_feature: dict[str, int] = Field(default_factory=dict, description="简体中文业务功能调用次数统计")
    run_types_breakdown: list[RunTypeMetricItem] = Field(default_factory=list, description="结构化简体中文业务功能调用分布明细")
    success_rate: float = 100.0


class TokenUsageMetrics(BaseModel):
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    analysis_runs_count: int = 0
    runs_by_type: dict[str, int] = Field(default_factory=dict)
    runs_by_feature: dict[str, int] = Field(default_factory=dict, description="简体中文业务功能调用次数统计")
    run_types_breakdown: list[RunTypeMetricItem] = Field(default_factory=list, description="结构化简体中文业务功能调用分布明细")
    models_breakdown: list[ModelTokenUsage] = Field(default_factory=list, description="按大模型名称细分统计的消耗数据")
    hourly_scope: TokenScopeMetrics = Field(default_factory=TokenScopeMetrics, description="今日 24 小时分时维度对应的模型与功能消耗细分")
    daily_scope: TokenScopeMetrics = Field(default_factory=TokenScopeMetrics, description="所选周期分天维度对应的模型与功能消耗细分")
    token_hourly_trend: list[HourlyTrendPoint] = Field(default_factory=list, description="当天 24 小时分时 Token 消耗与调用走势")
    token_daily_trend: list[TokenTrendPoint] = Field(default_factory=list, description="近期连续分天 Token 消耗与调用走势")
    avg_duration_ms: float = 0.0
    tokens_per_second: float = 0.0
    active_model: str = ""
    success_rate: float = 100.0



class PeriodComparison(BaseModel):
    messages_growth_pct: Optional[float] = None
    episodes_growth_pct: Optional[float] = None
    topics_growth_pct: Optional[float] = None
    insights_growth_pct: Optional[float] = None
    comparison_label: str = ""
    comparison_start_date: str = ""
    comparison_end_date: str = ""


class OperationalOverview(BaseModel):
    period: str = "7d"
    start_date: str = ""
    end_date: str = ""
    days_count: int = 7
    total_messages: int = 0
    daily_avg_messages: float = 0.0
    total_conversations: int = 0
    total_active_users: int = 0
    total_episodes: int = 0
    total_topics: int = 0
    total_insights: int = 0
    total_pushed_insights: int = 0
    total_research_queries: int = 0
    token_usage: TokenUsageMetrics = Field(default_factory=TokenUsageMetrics)
    comparison: PeriodComparison = Field(default_factory=PeriodComparison)
    daily_trends: list[DailyTrendPoint] = Field(default_factory=list)
    brief_summary: str = ""


class SubCategoryItem(BaseModel):
    name: str
    count: int


class CategoryDynamicsItem(BaseModel):
    category: str
    category_zh: str
    topic_count: int = 0
    insight_count: int = 0
    total_count: int = 0
    change_pct: Optional[float] = None
    alert_level: str = "normal"  # normal | warning | critical
    sub_categories: list[SubCategoryItem] = Field(default_factory=list)


class HighValueTopicItem(BaseModel):
    topic_id: str
    title: str
    module: str
    module_zh: str
    severity: str
    severity_zh: str
    feedback_count: int = 1
    unique_users: int = 1
    summary: str
    core_quote: Optional[str] = None
    associated_entities: list[str] = Field(default_factory=list)
    episode_id: Optional[str] = None
    user_quotes: list[str] = Field(default_factory=list)
    suggested_action: Optional[str] = None


class CriticalInsightItem(BaseModel):
    insight_id: str
    title: str
    module: str
    module_zh: str
    severity: str
    severity_zh: str
    priority: str = "P1"
    symptom: str
    root_cause: Optional[str] = None
    verbatim_quote: Optional[str] = None
    actionable_direction: Optional[str] = None
    evidence_count: int = 0
    drilldown_uri: str = ""


class HighValueContent(BaseModel):
    category_dynamics: list[CategoryDynamicsItem] = Field(default_factory=list)
    high_value_topics: list[HighValueTopicItem] = Field(default_factory=list)
    critical_insights: list[CriticalInsightItem] = Field(default_factory=list)
    ai_executive_summary: Optional[str] = None
    key_recommendations: list[str] = Field(default_factory=list)
    rag_evidence_highlights: list[dict[str, Any]] = Field(default_factory=list, description="由 RAG 向量检索库召回的当期社群典型原声切片与事实依据")


class VoCReportOutput(BaseModel):
    period_label: str = Field(description="报告周期标签（如 2026年第36周社群用户反馈洞察报告）")
    period: str = "7d"
    operational_overview: OperationalOverview = Field(default_factory=OperationalOverview)
    high_value_content: HighValueContent = Field(default_factory=HighValueContent)
    generated_at: str = ""

    # Legacy fields for backward compatibility
    total_feedbacks: int = 0
    total_topics: int = 0
    total_unique_users: int = 0
    module_distribution: list[MetricDistribution] = Field(default_factory=list)
    severity_distribution: list[MetricDistribution] = Field(default_factory=list)
    type_distribution: list[MetricDistribution] = Field(default_factory=list)
    top_critical_topics: list[TopicMetricItem] = Field(default_factory=list)
    executive_summary: str = ""
    key_recommendations: list[str] = Field(default_factory=list)


class FeishuConfig(BaseModel):
    webhook_url: str = ""
    secret: Optional[str] = None
    enabled: bool = True
    auto_scheduled: bool = False
    scheduled_cron: str = "0 9 * * 1"  # Every Monday at 09:00
    report_period: str = "7d"
    last_push_at: Optional[str] = None
    last_push_status: Optional[str] = None


class FeishuPushResponse(BaseModel):
    success: bool
    status_code: int = 200
    message: str = ""
    feishu_log_id: Optional[str] = None

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


class SearchFilter(BaseModel):
    modules: list[str] = Field(default_factory=list)
    insight_types: list[str] = Field(default_factory=list)
    severities: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


class SearchResultItem(BaseModel):
    entity_type: str = Field(description="topic | insight | message | media")
    entity_id: str
    title: str
    snippet: str
    score: float
    evidence_uri: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class HybridSearchResponse(BaseModel):
    query: str
    total_hits: int
    results: list[SearchResultItem]
    duration_ms: float


class Citation(BaseModel):
    citation_id: int
    evidence_uri: str
    title: str
    snippet: str
    entity_type: str = "insight"  # insight | topic | episode | message
    entity_id: str = ""
    module: Optional[str] = None
    module_zh: Optional[str] = None
    severity: Optional[str] = None
    severity_label_zh: Optional[str] = None
    insight_type: Optional[str] = None
    type_label_zh: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    episode_id: Optional[str] = None
    conversation_name: Optional[str] = None
    parsed_5w1h: Optional[dict[str, str]] = None
    full_description: Optional[str] = None


class RequirementItem(BaseModel):
    req_id: str = Field(description="需求编号，如 REQ-1")
    title: str = Field(description="需求标题（15字以内）")
    category: str = Field(default="综合体验", description="所属业务分类")
    severity: str = Field(default="medium", description="严重程度/优先级: blocker | major | minor | trivial")
    problem_statement: str = Field(description="痛点与具体场景描述")
    user_voice: Optional[str] = Field(default=None, description="真实群聊用户原声引用")
    recommended_action: str = Field(description="落地改进方案与建议")
    citation_ids: list[int] = Field(default_factory=list, description="引用的证据编号列表，如 [1, 2]")


class ResearchAssistantResponse(BaseModel):
    question: str
    executive_summary: Optional[str] = None
    requirements: list[RequirementItem] = Field(default_factory=list)
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    key_findings: list[str] = Field(default_factory=list)
    related_topic_ids: list[str] = Field(default_factory=list)
    related_insight_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.95
    from_history: bool = False
    history_id: Optional[str] = None
    created_at: Optional[str] = None
    duration_ms: Optional[float] = None
    model_used: Optional[str] = None

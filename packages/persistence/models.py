from datetime import datetime
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from packages.domain.models import generate_id
from packages.persistence.db import Base


def utcnow():
    return datetime.now()


class Workspace(Base):
    __tablename__ = "workspace"

    id = Column(String(36), primary_key=True, default=generate_id)
    name = Column(String(128), nullable=False)
    timezone = Column(String(64), nullable=False, default="Asia/Shanghai")
    salt_hex = Column(String(64), nullable=False, default="00112233445566778899aabbccddeeff")
    settings_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class SourceRoot(Base):
    __tablename__ = "source_root"

    id = Column(String(36), primary_key=True, default=generate_id)
    workspace_id = Column(String(36), ForeignKey("workspace.id"), nullable=False)
    source_type = Column(String(64), nullable=False, default="wechat_archive")
    display_name = Column(String(128), nullable=False)
    root_path = Column(String(512), nullable=False)
    read_only = Column(Boolean, nullable=False, default=True)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("workspace_id", "display_name", name="uq_source_root_name"),)


class SourceFile(Base):
    __tablename__ = "source_file"

    id = Column(String(36), primary_key=True, default=generate_id)
    workspace_id = Column(String(36), ForeignKey("workspace.id"), nullable=False)
    source_root_id = Column(String(36), ForeignKey("source_root.id"), nullable=False)
    relative_path = Column(String(512), nullable=False)
    file_kind = Column(String(32), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    mtime_ns = Column(BigInteger, nullable=True)
    sha256 = Column(String(64), nullable=False)
    discovered_at = Column(DateTime, nullable=False, default=utcnow)
    last_seen_at = Column(DateTime, nullable=False, default=utcnow)
    state = Column(String(32), nullable=False, default="active")

    __table_args__ = (UniqueConstraint("source_root_id", "relative_path", "sha256", name="uq_source_file"),)


class Conversation(Base):
    __tablename__ = "conversation"

    id = Column(String(36), primary_key=True, default=generate_id)
    workspace_id = Column(String(36), ForeignKey("workspace.id"), nullable=False)
    source_type = Column(String(64), nullable=False, default="wechat_archive")
    source_conversation_id = Column(String(128), nullable=True)
    display_name = Column(String(256), nullable=False)
    conversation_kind = Column(String(32), nullable=False, default="group")
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("workspace_id", "source_type", "source_conversation_id", name="uq_conversation_source"),)


class Participant(Base):
    __tablename__ = "participant"

    id = Column(String(36), primary_key=True, default=generate_id)
    workspace_id = Column(String(36), ForeignKey("workspace.id"), nullable=False)
    stable_anonymous_key = Column(String(64), nullable=False)
    display_label = Column(String(128), nullable=False)
    participant_type = Column(String(32), nullable=False, default="unknown")
    role = Column(String(32), nullable=False, default="user")
    is_internal = Column(Boolean, nullable=False, default=False)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("workspace_id", "stable_anonymous_key", name="uq_participant_key"),)


class ParticipantAlias(Base):
    __tablename__ = "participant_alias"

    id = Column(String(36), primary_key=True, default=generate_id)
    participant_id = Column(String(36), ForeignKey("participant.id"), nullable=False)
    source_type = Column(String(64), nullable=False, default="wechat_archive")
    source_participant_id = Column(String(128), nullable=True)
    display_name_ciphertext = Column(Text, nullable=True)
    display_name_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class Message(Base):
    __tablename__ = "message"

    id = Column(String(36), primary_key=True, default=generate_id)
    workspace_id = Column(String(36), ForeignKey("workspace.id"), nullable=False)
    conversation_id = Column(String(36), ForeignKey("conversation.id"), nullable=False)
    participant_id = Column(String(36), ForeignKey("participant.id"), nullable=True)
    source_file_id = Column(String(36), ForeignKey("source_file.id"), nullable=False)
    source_message_id = Column(String(128), nullable=True)
    source_sequence = Column(BigInteger, nullable=False)
    source_line_start = Column(Integer, nullable=True)
    source_line_end = Column(Integer, nullable=True)
    sent_at = Column(DateTime, nullable=False)
    source_timezone = Column(String(64), nullable=False, default="Asia/Shanghai")
    raw_text = Column(Text, nullable=False)
    normalized_text = Column(Text, nullable=False)
    searchable_text = Column(Text, nullable=False, default="")
    quoted_message_id = Column(String(36), ForeignKey("message.id"), nullable=True)
    quote_unresolved_text = Column(Text, nullable=True)
    mentions_json = Column(JSON, nullable=False, default=list)
    raw_payload_json = Column(JSON, nullable=False, default=dict)
    source_record_hash = Column(String(64), nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    imported_at = Column(DateTime, nullable=False, default=utcnow)
    deleted_at = Column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("workspace_id", "source_record_hash", name="uq_message_source_hash"),)


class MediaAsset(Base):
    __tablename__ = "media_asset"

    id = Column(String(36), primary_key=True, default=generate_id)
    workspace_id = Column(String(36), ForeignKey("workspace.id"), nullable=False)
    source_file_id = Column(String(36), ForeignKey("source_file.id"), nullable=False)
    kind = Column(String(32), nullable=False)
    mime_type = Column(String(64), nullable=True)
    sha256 = Column(String(64), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    duration_ms = Column(BigInteger, nullable=True)
    derived_preview_relpath = Column(String(512), nullable=True)
    state = Column(String(32), nullable=False, default="available")
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("workspace_id", "sha256", name="uq_media_asset_sha"),)


class MessageMediaLink(Base):
    __tablename__ = "message_media_link"

    id = Column(String(36), primary_key=True, default=generate_id)
    message_id = Column(String(36), ForeignKey("message.id"), nullable=False)
    media_id = Column(String(36), ForeignKey("media_asset.id"), nullable=False)
    media_order = Column(Integer, nullable=False, default=1)
    method = Column(String(64), nullable=False)
    confidence = Column(Float, nullable=False)
    state = Column(String(32), nullable=False)
    confirmed_by = Column(String(36), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    evidence_json = Column(JSON, nullable=False, default=dict)
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("message_id", "media_id", "revision", name="uq_msg_media_link"),)


class AnalysisRun(Base):
    __tablename__ = "analysis_run"

    id = Column(String(36), primary_key=True, default=generate_id)
    workspace_id = Column(String(36), ForeignKey("workspace.id"), nullable=False)
    run_type = Column(String(64), nullable=False)  # image_enrichment | video_enrichment | episode | insight | pairwise_judge
    target_type = Column(String(64), nullable=False)  # media_asset | episode | insight | topic
    target_id = Column(String(36), nullable=False)
    provider = Column(String(64), nullable=False)
    model = Column(String(128), nullable=False)
    prompt_key = Column(String(128), nullable=True)
    prompt_version = Column(String(32), nullable=True)
    schema_version = Column(String(32), nullable=False, default="v2")
    input_hash = Column(String(64), nullable=False)
    config_hash = Column(String(64), nullable=False)
    state = Column(String(32), nullable=False, default="running")  # running | succeeded | failed | stale
    started_at = Column(DateTime, nullable=False, default=utcnow)
    completed_at = Column(DateTime, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    cost_json = Column(JSON, nullable=True)
    error_json = Column(JSON, nullable=True)
    raw_response = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("run_type", "target_type", "target_id", "input_hash", "config_hash", name="uq_analysis_run_cache"),)


class MediaEnrichment(Base):
    __tablename__ = "media_enrichment"

    id = Column(String(36), primary_key=True, default=generate_id)
    workspace_id = Column(String(36), ForeignKey("workspace.id"), nullable=False)
    media_id = Column(String(36), ForeignKey("media_asset.id"), nullable=False)
    analysis_run_id = Column(String(36), ForeignKey("analysis_run.id"), nullable=True)
    revision = Column(Integer, nullable=False, default=1)
    state = Column(String(32), nullable=False, default="available")  # available | partial | failed | superseded
    summary = Column(Text, nullable=True)
    searchable_text = Column(Text, nullable=False, default="")
    ocr_json = Column(JSON, nullable=True)
    asr_json = Column(JSON, nullable=True)
    visual_json = Column(JSON, nullable=True)
    timeline_json = Column(JSON, nullable=True)
    uncertainty_json = Column(JSON, nullable=False, default=list)
    human_override_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    superseded_at = Column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("media_id", "revision", name="uq_media_enrichment_rev"),)


class Episode(Base):
    __tablename__ = "episode"

    id = Column(String(36), primary_key=True, default=generate_id)
    workspace_id = Column(String(36), ForeignKey("workspace.id"), nullable=False)
    conversation_id = Column(String(36), ForeignKey("conversation.id"), nullable=False)
    title = Column(String(256), nullable=False)
    summary = Column(Text, nullable=False)
    category_hint = Column(String(64), nullable=False, default="general")
    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime, nullable=False)
    message_count = Column(Integer, nullable=False, default=0)
    participants_json = Column(JSON, nullable=False, default=list)
    media_json = Column(JSON, nullable=False, default=list)
    state = Column(String(32), nullable=False, default="active")
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class EpisodeMessage(Base):
    __tablename__ = "episode_message"

    id = Column(String(36), primary_key=True, default=generate_id)
    episode_id = Column(String(36), ForeignKey("episode.id"), nullable=False)
    message_id = Column(String(36), ForeignKey("message.id"), nullable=False)
    sequence = Column(Integer, nullable=False, default=1)

    __table_args__ = (UniqueConstraint("episode_id", "message_id", name="uq_episode_msg"),)


class Insight(Base):
    __tablename__ = "insight"

    id = Column(String(36), primary_key=True, default=generate_id)
    workspace_id = Column(String(36), ForeignKey("workspace.id"), nullable=False)
    episode_id = Column(String(36), ForeignKey("episode.id"), nullable=False)
    source_type = Column(String(64), nullable=False, default="wechat_archive")
    insight_type = Column(String(32), nullable=False)  # issue | feature_request | inquiry | praise
    module = Column(String(128), nullable=False)
    sub_module = Column(String(128), nullable=True)
    severity = Column(String(32), nullable=False, default="minor")  # blocker | major | minor | trivial
    priority = Column(String(32), nullable=False, default="P2")
    device_model = Column(String(64), nullable=True)  # 关联设备机型，如 C2, U1 等，无法明确则为空
    summary = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    status_in_chat = Column(String(64), nullable=False, default="unresolved")  # unresolved | support_acknowledged | workaround_provided | fix_confirmed
    support_known_status = Column(Boolean, nullable=False, default=False)
    factual_score = Column(Float, nullable=False, default=1.0)
    confidence = Column(Float, nullable=False, default=0.9)
    tags_json = Column(JSON, nullable=False, default=list)
    state = Column(String(32), nullable=False, default="draft")  # draft | reviewing | approved | rejected | archived
    reviewed_by = Column(String(36), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    analysis_run_id = Column(String(36), ForeignKey("analysis_run.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class InsightClaim(Base):
    __tablename__ = "insight_claim"

    id = Column(String(36), primary_key=True, default=generate_id)
    insight_id = Column(String(36), ForeignKey("insight.id"), nullable=False)
    claim_key = Column(String(64), nullable=False)
    claim_text = Column(Text, nullable=False)
    fact_category = Column(String(64), nullable=False, default="symptom")
    evidence_uris_json = Column(JSON, nullable=False, default=list)
    confidence = Column(Float, nullable=False, default=1.0)
    verification_state = Column(String(32), nullable=False, default="supported")
    created_at = Column(DateTime, nullable=False, default=utcnow)


class Topic(Base):
    __tablename__ = "topic"

    id = Column(String(36), primary_key=True, default=generate_id)
    workspace_id = Column(String(36), ForeignKey("workspace.id"), nullable=False)
    title = Column(String(256), nullable=False)
    summary = Column(Text, nullable=False)
    module = Column(String(128), nullable=False)
    sub_module = Column(String(128), nullable=True)
    severity = Column(String(32), nullable=False, default="minor")  # blocker | major | minor | trivial
    status = Column(String(32), nullable=False, default="open")      # open | in_progress | resolved | closed | ignored
    feedback_count = Column(Integer, nullable=False, default=1)
    unique_users_count = Column(Integer, nullable=False, default=1)
    first_seen_at = Column(DateTime, nullable=False, default=utcnow)
    last_seen_at = Column(DateTime, nullable=False, default=utcnow)
    tags_json = Column(JSON, nullable=False, default=list)
    abc_sync_state = Column(String(32), nullable=False, default="not_synced")  # not_synced | synced | sync_failed
    abc_external_id = Column(String(128), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class TopicInsightLink(Base):
    __tablename__ = "topic_insight_link"

    id = Column(String(36), primary_key=True, default=generate_id)
    topic_id = Column(String(36), ForeignKey("topic.id"), nullable=False)
    insight_id = Column(String(36), ForeignKey("insight.id"), nullable=False)
    relation_type = Column(String(32), nullable=False, default="instance")  # instance | sub_issue
    created_at = Column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("topic_id", "insight_id", name="uq_topic_insight_link"),)


class ImportBatch(Base):
    __tablename__ = "import_batch"

    id = Column(String(36), primary_key=True, default=generate_id)
    workspace_id = Column(String(36), ForeignKey("workspace.id"), nullable=False)
    source_root_id = Column(String(36), ForeignKey("source_root.id"), nullable=False)
    batch_key = Column(String(128), nullable=False)
    date_str = Column(String(32), nullable=False)
    conversation_name = Column(String(256), nullable=False)
    input_hash = Column(String(64), nullable=False)
    state = Column(String(32), nullable=False, default="queued")
    summary_json = Column(JSON, nullable=False, default=dict)
    quality_report_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("workspace_id", "batch_key", name="uq_import_batch_key"),)


class Job(Base):
    __tablename__ = "job"

    id = Column(String(36), primary_key=True, default=generate_id)
    workspace_id = Column(String(36), ForeignKey("workspace.id"), nullable=False)
    job_type = Column(String(64), nullable=False)
    unique_key = Column(String(128), nullable=False)
    priority = Column(Integer, nullable=False, default=10)
    payload_json = Column(JSON, nullable=False, default=dict)
    state = Column(String(32), nullable=False, default="queued")
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    run_after = Column(DateTime, nullable=False, default=utcnow)
    lease_owner = Column(String(128), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    last_error_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("workspace_id", "unique_key", name="uq_job_unique_key"),)


class AuditEvent(Base):
    __tablename__ = "audit_event"

    id = Column(String(36), primary_key=True, default=generate_id)
    workspace_id = Column(String(36), ForeignKey("workspace.id"), nullable=False)
    actor_type = Column(String(32), nullable=False, default="user")
    actor_id = Column(String(64), nullable=False, default="system")
    action = Column(String(64), nullable=False)
    object_type = Column(String(64), nullable=False)
    object_id = Column(String(64), nullable=False)
    before_hash = Column(String(64), nullable=True)
    after_hash = Column(String(64), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    trace_id = Column(String(64), nullable=True)
    occurred_at = Column(DateTime, nullable=False, default=utcnow)


class DomainEventOutbox(Base):
    __tablename__ = "domain_event_outbox"

    id = Column(String(36), primary_key=True, default=generate_id)
    event_type = Column(String(64), nullable=False)
    aggregate_type = Column(String(64), nullable=False)
    aggregate_id = Column(String(64), nullable=False)
    aggregate_revision = Column(Integer, nullable=False, default=1)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    payload_json = Column(JSON, nullable=False, default=dict)
    state = Column(String(32), nullable=False, default="queued")
    created_at = Column(DateTime, nullable=False, default=utcnow)


class TaskExecution(Base):
    __tablename__ = "task_execution"

    id = Column(String(36), primary_key=True, default=generate_id)
    workspace_id = Column(String(36), ForeignKey("workspace.id"), nullable=True)
    task_type = Column(String(64), nullable=False)  # full_pipeline | scan_import | multimodal | segmentation | insights | clustering | abc_sync
    title = Column(String(256), nullable=False)
    status = Column(String(32), nullable=False, default="running")  # running | completed | failed | warning | cancelled
    progress_pct = Column(Integer, nullable=False, default=0)
    current_step_name = Column(String(128), nullable=True)
    current_item_label = Column(String(256), nullable=True)
    params_json = Column(JSON, nullable=False, default=dict)
    summary_json = Column(JSON, nullable=False, default=dict)
    steps_json = Column(JSON, nullable=False, default=list)
    error_summary = Column(Text, nullable=True)
    error_detail = Column(Text, nullable=True)
    duration_ms = Column(Float, nullable=False, default=0.0)
    started_at = Column(DateTime, nullable=False, default=utcnow)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class ResearchQueryRecord(Base):
    __tablename__ = "research_query_record"

    id = Column(String(36), primary_key=True, default=generate_id)
    workspace_id = Column(String(36), ForeignKey("workspace.id"), nullable=False, index=True)
    question = Column(String(512), nullable=False, index=True)
    executive_summary = Column(Text, nullable=True)
    answer_text = Column(Text, nullable=False)
    requirements_json = Column(JSON, nullable=False, default=list)
    citations_json = Column(JSON, nullable=False, default=list)
    key_findings_json = Column(JSON, nullable=False, default=list)
    related_topic_ids_json = Column(JSON, nullable=False, default=list)
    related_insight_ids_json = Column(JSON, nullable=False, default=list)
    confidence = Column(Float, nullable=False, default=0.95)
    model_used = Column(String(128), nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    duration_ms = Column(Float, nullable=True, default=0.0)
    is_bookmarked = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class InsightPushRecord(Base):
    __tablename__ = "insight_push_record"

    id = Column(String(36), primary_key=True, default=generate_id)
    workspace_id = Column(String(36), ForeignKey("workspace.id"), nullable=False, index=True)
    insight_id = Column(String(36), ForeignKey("insight.id"), nullable=False, index=True)
    target_platform = Column(String(32), nullable=False, default="feishu_bitable")
    webhook_url = Column(String(512), nullable=False)
    payload_json = Column(JSON, nullable=False, default=dict)
    state = Column(String(32), nullable=False, default="success")  # success | failed
    status_code = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    operator = Column(String(64), nullable=False, default="user")
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)


class MessageContextChunk(Base):
    """Level 1: 消息上下文微切片向量索引表 (带前后文窗口与多模态OCR)"""
    __tablename__ = "message_context_chunk"

    id = Column(String(36), primary_key=True, default=generate_id)
    workspace_id = Column(String(36), ForeignKey("workspace.id"), nullable=False, index=True)
    conversation_id = Column(String(36), ForeignKey("conversation.id"), nullable=False, index=True)
    center_message_id = Column(String(36), ForeignKey("message.id"), nullable=False, index=True)
    context_text = Column(Text, nullable=False)
    text_sha256 = Column(String(64), nullable=False, index=True)
    embedding = Column(LargeBinary, nullable=True)
    embedding_model = Column(String(64), nullable=True)
    dimension = Column(Integer, nullable=True, default=1536)
    created_at = Column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("center_message_id", "embedding_model", name="uq_chunk_msg_model"),
    )


class TopicVector(Base):
    """Level 2: 知识库主题与洞察语义向量索引表"""
    __tablename__ = "topic_vector"

    id = Column(String(36), primary_key=True, default=generate_id)
    workspace_id = Column(String(36), ForeignKey("workspace.id"), nullable=False, index=True)
    topic_id = Column(String(36), ForeignKey("topic.id"), nullable=False, index=True)
    text_repr = Column(Text, nullable=False)
    text_sha256 = Column(String(64), nullable=False, index=True)
    embedding = Column(LargeBinary, nullable=False)
    embedding_model = Column(String(64), nullable=False)
    dimension = Column(Integer, nullable=False, default=1536)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("topic_id", "embedding_model", name="uq_topic_vector_model"),
    )




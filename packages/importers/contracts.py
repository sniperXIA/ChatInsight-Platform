from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator

from packages.domain.enums import MediaKind, MediaLinkState


class SourceSender(BaseModel):
    source_participant_id: str | None = None
    display_name: str
    role_hint: str | None = None


class SourceMediaRef(BaseModel):
    media_external_id: str
    order: int = Field(default=1, ge=1)


class SourceMessage(BaseModel):
    schema_version: int = 2
    source_message_id: str
    sequence: int = Field(ge=1)
    sent_at: datetime
    sender: SourceSender
    text: str = ""
    quoted_message_id: str | None = None
    quote_text_unresolved: str | None = None
    mentions: list[str] = Field(default_factory=list)
    media_refs: list[SourceMediaRef] = Field(default_factory=list)
    source_line_start: int | None = None
    source_line_end: int | None = None
    raw_payload_json: dict[str, Any] = Field(default_factory=dict)


class SourceMedia(BaseModel):
    schema_version: int = 2
    media_external_id: str
    source_message_id: str | None = None
    order: int = Field(default=1, ge=1)
    kind: MediaKind = MediaKind.IMAGE
    relative_path: str
    sha256: str
    size_bytes: int = Field(ge=0)
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("relative_path must not traverse outside the batch root")
        return str(path)


class SourceBatchManifest(BaseModel):
    schema_version: int = 2
    batch_id: str
    source: str = "wechat_archive"
    conversation_external_id: str
    conversation_name: str
    timezone: str = "Asia/Shanghai"
    exported_at: datetime
    message_file: str = "messages.jsonl"
    media_manifest_file: str = "media_manifest.jsonl"
    crawler_version: str | None = "2.0.0"
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class UnattachedLine(BaseModel):
    line_number: int
    raw_text: str
    reason: str


class LegacyMessageDraft(BaseModel):
    source_line_start: int
    source_line_end: int
    timestamp_text: str
    sender_text: str
    body_lines: list[str]
    raw_text: str = ""
    sent_at: datetime | None = None
    extracted_quotes: list[str] = Field(default_factory=list)
    extracted_media_names: list[str] = Field(default_factory=list)
    has_image_placeholder: bool = False
    mentions: list[str] = Field(default_factory=list)


class MediaCandidateLink(BaseModel):
    message_id: str
    media_asset_id: str
    media_filename: str
    media_order: int = 1
    method: Literal["explicit_filename", "manifest", "timestamp_proximity_unique", "order_candidate", "manual"]
    confidence: float = Field(ge=0.0, le=1.0)
    state: MediaLinkState = MediaLinkState.NEEDS_REVIEW
    evidence_json: dict[str, Any] = Field(default_factory=dict)


class DiagnosticIssue(BaseModel):
    code: str
    source_file: str | None = None
    line: int | None = None
    message: str


class PrecheckSummary(BaseModel):
    conversation_count: int = 0
    message_drafts: int = 0
    media_files: int = 0
    explicit_media_links: int = 0
    unresolved_image_placeholders: int = 0
    unresolved_quotes: int = 0
    parse_errors: int = 0


class PrecheckReport(BaseModel):
    scan_id: str
    source_root_path: str
    summary: PrecheckSummary
    blocking_issues: list[DiagnosticIssue] = Field(default_factory=list)
    warnings: list[DiagnosticIssue] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.now)
    batches: list[dict[str, Any]] = Field(default_factory=list)


class ParsedBatch(BaseModel):
    batch_key: str
    date_str: str
    conversation_name: str
    source_file_path: str
    source_file_sha256: str
    messages: list[SourceMessage]
    media_items: list[SourceMedia]
    media_links: list[MediaCandidateLink]
    unattached_lines: list[UnattachedLine] = Field(default_factory=list)
    input_hash: str

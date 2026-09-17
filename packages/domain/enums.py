from enum import StrEnum


class SourceType(StrEnum):
    WECHAT_ARCHIVE = "wechat_archive"
    API_INGEST = "api_ingest"
    FILE_UPLOAD = "file_upload"


class ConversationKind(StrEnum):
    GROUP = "group"
    DIRECT = "direct"
    SYSTEM = "system"


class ParticipantRole(StrEnum):
    USER = "user"
    SUPPORT = "support"
    DEVELOPER = "developer"
    PRODUCT_MANAGER = "product_manager"
    BOT = "bot"
    UNKNOWN = "unknown"


class MediaKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"


class MediaLinkState(StrEnum):
    CONFIRMED = "confirmed"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ImportBatchState(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReviewState(StrEnum):
    DRAFT = "draft"
    NEEDS_REVIEW = "needs_review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class SeverityLevel(StrEnum):
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"
    TRIVIAL = "trivial"


class FactualState(StrEnum):
    OBSERVED = "observed"
    REPRODUCED_BY_OTHERS = "reproduced_by_others"
    WORKAROUND_FOUND = "workaround_found"
    ACKNOWLEDGED_BY_SUPPORT = "acknowledged_by_support"
    ROOT_CAUSE_CONFIRMED = "root_cause_confirmed"
    FIXED_CANDIDATE = "fixed_candidate"
    FIXED_CONFIRMED = "fixed_confirmed"
    NOT_REPRODUCIBLE = "not_reproducible"
    INVALID_OR_MISUNDERSTANDING = "invalid_or_misunderstanding"


class InsightType(StrEnum):
    ISSUE = "issue"
    FEATURE_REQUEST = "feature_request"
    INQUIRY = "inquiry"
    PRAISE = "praise"
    PRODUCT_ISSUE = "product_issue"
    EXPLICIT_REQUIREMENT = "explicit_requirement"
    LATENT_NEED = "latent_need"
    USABILITY_OPPORTUNITY = "usability_opportunity"
    DOCUMENTATION_GAP = "documentation_gap"
    CONSULTATION = "consultation"
    POSITIVE_SIGNAL = "positive_signal"
    NON_PRODUCT = "non_product"


class EvidenceKind(StrEnum):
    MESSAGE = "message"
    IMAGE = "image"
    VIDEO_SEGMENT = "video_segment"
    EPISODE = "episode"
    DOCUMENT_CHUNK = "document_chunk"
    SUPPORT_CONFIRMATION = "support_confirmation"
    RELEASE_NOTE = "release_note"

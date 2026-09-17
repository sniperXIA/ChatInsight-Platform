import hashlib
import hmac
import json
import uuid
from typing import Any


def generate_id() -> str:
    """Generate a standard UUID string."""
    return str(uuid.uuid4())


def canonical_json_hash(value: Any) -> str:
    """Generate deterministic SHA256 hash for JSON-serializable structures."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_text(text: str) -> str:
    """Generate SHA256 hex string for given text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    """Calculate SHA256 hex digest of a file in chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def anonymous_label(workspace_salt: bytes, source_identity: str) -> str:
    """Generate stable anonymous label (e.g. 用户 U-6F31) using HMAC-SHA256."""
    digest = hmac.new(workspace_salt, source_identity.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"用户 U-{digest[:6].upper()}"


class DomainError(Exception):
    """Base domain exception."""

    def __init__(self, message: str, code: str = "DOMAIN_ERROR", details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class EntityNotFoundError(DomainError):
    def __init__(self, entity_name: str, entity_id: str):
        super().__init__(
            f"{entity_name} with id {entity_id} was not found",
            code="ENTITY_NOT_FOUND",
            details={"entity": entity_name, "id": entity_id},
        )


class ValidationError(DomainError):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message, code="VALIDATION_ERROR", details={"field": field} if field else {})

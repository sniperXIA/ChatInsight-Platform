import re
from datetime import datetime
from typing import Tuple

from packages.domain.models import sha256_text
from packages.importers.contracts import (
    LegacyMessageDraft,
    SourceMessage,
    SourceSender,
    UnattachedLine,
)

# Robust message header regex: 【YYYY-MM-DD HH:MM:SS】Sender: Body (handles both Chinese and English colons)
MESSAGE_HEADER_RE = re.compile(
    r"^【(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})】(?P<sender>.*?)[：:](?P<body>.*)$"
)

# Media filename pattern inside brackets: e.g. [32hexchars.mp4], [32hexchars.jpg], [video_123.mp4]
MEDIA_FILE_RE = re.compile(r"\[([a-zA-Z0-9_\-\.]+\.(?:mp4|mov|avi|mkv|jpg|jpeg|png|webp|gif))\]", re.IGNORECASE)

# Mentions regex: @nickname or @nickname\u2005
MENTION_RE = re.compile(r"@([^\s\u2005@]+)[\s\u2005]?")


class LegacyTxtParser:
    """Parser for legacy WeChat group export text files."""

    def __init__(self, timezone_str: str = "Asia/Shanghai"):
        self.timezone_str = timezone_str

    def parse_lines(self, lines: list[str]) -> Tuple[list[LegacyMessageDraft], list[UnattachedLine]]:
        """Parse raw text lines into message drafts while tracking unattached lines."""
        drafts: list[LegacyMessageDraft] = []
        unattached: list[UnattachedLine] = []
        current: LegacyMessageDraft | None = None

        for line_number, raw_line in enumerate(lines, start=1):
            line = raw_line.rstrip("\r\n")
            match = MESSAGE_HEADER_RE.match(line)
            if match:
                if current is not None:
                    self._finalize_draft(current)
                    drafts.append(current)

                sender_text = match.group("sender").strip()
                body_text = match.group("body")

                # Detect WeChat export artifact where exporter sets sender to "未知" or "unknown"
                # but real sender and colon are prefixing the body: e.g. "tt8027: 瞎说"
                if sender_text in ("未知", "unknown", "") and body_text:
                    colon_m = re.match(r"^([a-zA-Z0-9_\-\u4e00-\u9fa5\s\.\(\)\[\]\+]+?)[：:](.*)$", body_text.strip())
                    if colon_m and colon_m.group(1).strip():
                        sender_text = colon_m.group(1).strip()
                        body_text = colon_m.group(2).lstrip()

                current = LegacyMessageDraft(
                    source_line_start=line_number,
                    source_line_end=line_number,
                    timestamp_text=match.group("timestamp").strip(),
                    sender_text=sender_text,
                    body_lines=[body_text],
                )
                continue

            if current is None:
                if line.strip():
                    unattached.append(
                        UnattachedLine(
                            line_number=line_number,
                            raw_text=line,
                            reason="Leading text before any recognized message header",
                        )
                    )
                continue

            # Multiline continuation
            current.body_lines.append(line)
            current.source_line_end = line_number

        if current is not None:
            self._finalize_draft(current)
            drafts.append(current)

        return drafts, unattached

    def _finalize_draft(self, draft: LegacyMessageDraft) -> None:
        """Process extracted body lines, media tokens, mentions, and quotes."""
        raw_text = "\n".join(draft.body_lines)
        draft.raw_text = raw_text

        # Parse timestamp
        try:
            draft.sent_at = datetime.strptime(draft.timestamp_text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            draft.sent_at = None

        # Check for [图片] placeholder
        draft.has_image_placeholder = "[图片]" in raw_text

        # Extract explicit media files
        explicit_medias = MEDIA_FILE_RE.findall(raw_text)
        draft.extracted_media_names = list(dict.fromkeys(explicit_medias))  # deduplicate preserving order

        # Check for [引用]
        if "[引用]" in raw_text:
            draft.extracted_quotes.append("[引用]")

        # Extract mentions
        mentions = MENTION_RE.findall(raw_text)
        draft.mentions = list(dict.fromkeys(mentions))

    def convert_drafts_to_messages(
        self,
        drafts: list[LegacyMessageDraft],
        source_file_sha256: str,
        conversation_external_id: str,
    ) -> list[SourceMessage]:
        """Convert LegacyMessageDrafts into standardized SourceMessages with idempotent IDs and quote resolutions."""
        messages: list[SourceMessage] = []

        for seq, draft in enumerate(drafts, start=1):
            sent_at = draft.sent_at or datetime.now()
            # Generate deterministic idempotent message ID
            id_payload = (
                f"{source_file_sha256}:{conversation_external_id}:{draft.source_line_start}:"
                f"{draft.source_line_end}:{draft.timestamp_text}:{draft.sender_text}:{draft.raw_text}"
            )
            msg_id = f"msg_{sha256_text(id_payload)[:24]}"

            role_hint = "user"
            sender_lower = draft.sender_text.lower()
            if any(k in sender_lower for k in ["客服", "官方", "助手", "技术支持", "support", "liberlive"]):
                role_hint = "support"

            msg = SourceMessage(
                schema_version=2,
                source_message_id=msg_id,
                sequence=seq,
                sent_at=sent_at,
                sender=SourceSender(
                    source_participant_id=None,
                    display_name=draft.sender_text,
                    role_hint=role_hint,
                ),
                text=draft.raw_text,
                quoted_message_id=None,
                quote_text_unresolved="[引用]" if draft.extracted_quotes else None,
                mentions=draft.mentions,
                media_refs=[],
                source_line_start=draft.source_line_start,
                source_line_end=draft.source_line_end,
                raw_payload_json={
                    "timestamp_text": draft.timestamp_text,
                    "extracted_media_names": draft.extracted_media_names,
                    "has_image_placeholder": draft.has_image_placeholder,
                },
            )
            messages.append(msg)

        # Resolve quotes heuristically within batch
        self._resolve_intra_batch_quotes(messages)
        return messages

    def _resolve_intra_batch_quotes(self, messages: list[SourceMessage]) -> None:
        """Heuristic resolution for [引用]: finds the preceding message from mentioned user or immediate previous message."""
        for i, msg in enumerate(messages):
            if msg.quote_text_unresolved == "[引用]" and not msg.quoted_message_id and i > 0:
                # If there are mentions, look for the most recent message by that sender
                if msg.mentions:
                    target_sender = msg.mentions[0]
                    for prev_msg in reversed(messages[:i]):
                        if target_sender in prev_msg.sender.display_name:
                            msg.quoted_message_id = prev_msg.source_message_id
                            break
                # Fallback: link to the immediately preceding message from a different sender if within 10 minutes
                if not msg.quoted_message_id and i > 0:
                    prev_msg = messages[i - 1]
                    time_diff = (msg.sent_at - prev_msg.sent_at).total_seconds()
                    if 0 <= time_diff <= 600 and prev_msg.sender.display_name != msg.sender.display_name:
                        msg.quoted_message_id = prev_msg.source_message_id

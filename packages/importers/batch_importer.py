from datetime import datetime
from pathlib import Path
from typing import Any
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models import sha256_text
from packages.importers.contracts import ParsedBatch
from packages.persistence.repositories.registry import RepositoryRegistry


class ImportStats(BaseModel):
    batch_key: str
    messages_processed: int = 0
    messages_inserted: int = 0
    media_processed: int = 0
    media_inserted: int = 0
    media_links_created: int = 0
    ambiguous_links_created: int = 0
    already_completed: bool = False
    duration_ms: float = 0.0


class BatchImporter:
    """Imports parsed batches into the persistence layer with full idempotency and transactional integrity."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = RepositoryRegistry(session)

    async def import_parsed_batch(
        self,
        batch: ParsedBatch,
        root_path: str,
        display_name: str = "默认数据源",
    ) -> ImportStats:
        t0 = datetime.now()
        stats = ImportStats(batch_key=batch.batch_key)

        # 1. Ensure Workspace
        ws = await self.repo.get_or_create_default_workspace()
        salt_bytes = bytes.fromhex(ws.salt_hex)

        # 2. Ensure SourceRoot
        root = await self.repo.get_or_create_source_root(
            workspace_id=ws.id,
            display_name=display_name,
            root_path=root_path,
        )

        # 3. Check or Start ImportBatch record
        batch_record = await self.repo.start_or_resume_batch(
            workspace_id=ws.id,
            source_root_id=root.id,
            batch_key=batch.batch_key,
            date_str=batch.date_str,
            conversation_name=batch.conversation_name,
            input_hash=batch.input_hash,
        )

        if batch_record.state == "completed" and batch_record.input_hash == batch.input_hash:
            stats.already_completed = True
            stats.duration_ms = (datetime.now() - t0).total_seconds() * 1000
            return stats

        # 4. Upsert SourceFile (TXT)
        txt_path = Path(batch.source_file_path)
        txt_size = txt_path.stat().st_size if txt_path.exists() else 0
        txt_rel_path = str(txt_path.relative_to(Path(root_path))).replace("\\", "/") if Path(root_path) in txt_path.parents else txt_path.name
        source_file = await self.repo.upsert_source_file(
            workspace_id=ws.id,
            source_root_id=root.id,
            relative_path=txt_rel_path,
            file_kind="chat_txt",
            size_bytes=txt_size,
            sha256=batch.source_file_sha256,
        )

        # 5. Upsert Conversation
        conv_external_id = f"wxgroup_{sha256_text(batch.conversation_name)[:16]}"
        conv = await self.repo.upsert_conversation(
            workspace_id=ws.id,
            source_type="wechat_archive",
            source_conversation_id=conv_external_id,
            display_name=batch.conversation_name,
        )

        # 6. Upsert Participants Map
        participant_map = {}
        for msg in batch.messages:
            disp_name = msg.sender.display_name
            if disp_name not in participant_map:
                p = await self.repo.get_or_create_participant(
                    workspace_id=ws.id,
                    workspace_salt=salt_bytes,
                    display_name=disp_name,
                    source_participant_id=msg.sender.source_participant_id,
                    role_hint=msg.sender.role_hint or "user",
                )
                participant_map[disp_name] = p

        # 7. Upsert Messages
        message_db_map = {}
        for msg in batch.messages:
            stats.messages_processed += 1
            p = participant_map.get(msg.sender.display_name)
            record_hash = sha256_text(
                f"{conv.id}:{msg.source_line_start}:{msg.source_line_end}:{msg.sent_at.isoformat()}:{msg.sender.display_name}:{msg.text}"
            )
            db_msg = await self.repo.upsert_message(
                workspace_id=ws.id,
                conversation_id=conv.id,
                participant_id=p.id if p else None,
                source_file_id=source_file.id,
                source_message_id=msg.source_message_id,
                sequence=msg.sequence,
                sent_at=msg.sent_at,
                raw_text=msg.text,
                source_record_hash=record_hash,
                source_line_start=msg.source_line_start,
                source_line_end=msg.source_line_end,
                quote_unresolved_text=msg.quote_text_unresolved,
                mentions_json=msg.mentions,
                raw_payload_json=msg.raw_payload_json,
            )
            message_db_map[msg.source_message_id] = db_msg
            stats.messages_inserted += 1

        # 8. Upsert Media Assets
        media_db_map = {}
        for media in batch.media_items:
            stats.media_processed += 1
            media_fpath = Path(batch.source_file_path).parent / media.relative_path
            m_rel_path = str(media_fpath.relative_to(Path(root_path))).replace("\\", "/") if Path(root_path) in media_fpath.parents else media.relative_path
            
            media_sf = await self.repo.upsert_source_file(
                workspace_id=ws.id,
                source_root_id=root.id,
                relative_path=m_rel_path,
                file_kind=media.kind.value,
                size_bytes=media.size_bytes,
                sha256=media.sha256,
            )

            db_media = await self.repo.upsert_media_asset(
                workspace_id=ws.id,
                source_file_id=media_sf.id,
                kind=media.kind.value,
                mime_type=media.mime_type or "application/octet-stream",
                sha256=media.sha256,
                size_bytes=media.size_bytes,
            )
            media_db_map[media.media_external_id] = db_media
            stats.media_inserted += 1

        # 9. Upsert Message-Media Links
        for link in batch.media_links:
            db_msg = message_db_map.get(link.message_id)
            db_media = media_db_map.get(link.media_asset_id)
            if db_msg and db_media:
                await self.repo.upsert_media_link(
                    message_id=db_msg.id,
                    media_id=db_media.id,
                    media_order=link.media_order,
                    method=link.method,
                    confidence=link.confidence,
                    state=link.state.value,
                    evidence_json=link.evidence_json,
                )
                stats.media_links_created += 1
                if link.state.value == "needs_review":
                    stats.ambiguous_links_created += 1

        # 10. Mark Batch Completed
        batch_record.state = "completed"
        batch_record.completed_at = datetime.now()
        batch_record.summary_json = {
            "messages_count": stats.messages_inserted,
            "media_count": stats.media_inserted,
            "links_count": stats.media_links_created,
            "ambiguous_links_count": stats.ambiguous_links_created,
        }

        # 11. Record Audit Event
        await self.repo.record_audit(
            workspace_id=ws.id,
            action="import.batch_completed",
            object_type="import_batch",
            object_id=batch_record.id,
            metadata_json=batch_record.summary_json,
        )

        stats.duration_ms = (datetime.now() - t0).total_seconds() * 1000
        return stats

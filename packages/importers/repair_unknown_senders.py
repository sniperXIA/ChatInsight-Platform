import asyncio
import logging
import re
from typing import Tuple

from sqlalchemy import delete, func, select

from packages.domain.models import sha256_text
from packages.persistence.db import get_session, init_db_engine
from packages.persistence.models import Message, Participant, Workspace
from packages.persistence.repositories.registry import RepositoryRegistry

logger = logging.getLogger("repair_unknown_senders")
logging.basicConfig(level=logging.INFO)

COLON_RE = re.compile(r"^([a-zA-Z0-9_\-\u4e00-\u9fa5\s\.\(\)\[\]\+]+?)[：:](.*)$")


async def repair_unknown_messages(dry_run: bool = False) -> Tuple[int, int]:
    """
    Find all messages currently linked to participants with display_label == '未知',
    extract the real sender from raw_text, re-link to the real participant,
    clean the raw_text, and remove the orphaned '未知' participant.
    """
    init_db_engine()
    migrated_count = 0
    created_participants = set()

    async for session in get_session():
        repo = RepositoryRegistry(session)

        # 1. Fetch unknown participants
        stmt_unknown_parts = select(Participant).where(
            (Participant.display_label == "未知") |
            (Participant.display_label == "unknown") |
            (Participant.display_label == "")
        )
        unknown_parts = (await session.execute(stmt_unknown_parts)).scalars().all()
        if not unknown_parts:
            logger.info("No unknown participants found in database. Everything is clean!")
            return 0, 0

        unknown_part_ids = [p.id for p in unknown_parts]
        logger.info(f"Found {len(unknown_parts)} unknown participant record(s): {unknown_part_ids}")

        # 2. Fetch all messages associated with these unknown participants
        stmt_msgs = (
            select(Message)
            .where(Message.participant_id.in_(unknown_part_ids))
            .order_by(Message.sent_at.asc())
        )
        messages = (await session.execute(stmt_msgs)).scalars().all()
        logger.info(f"Found {len(messages)} message(s) currently assigned to unknown participants.")

        # Cache workspace salt per workspace_id
        ws_cache = {}

        # 3. Migrate each message
        for msg in messages:
            if not msg.raw_text:
                continue

            match = COLON_RE.match(msg.raw_text.strip())
            if not match:
                logger.warning(f"Could not extract sender from message {msg.id}: {msg.raw_text[:40]}")
                continue

            real_sender = match.group(1).strip()
            clean_body = match.group(2).lstrip()

            if not real_sender:
                continue

            # Get workspace salt
            ws_id = msg.workspace_id
            if ws_id not in ws_cache:
                ws = (await session.execute(select(Workspace).where(Workspace.id == ws_id))).scalar_one_or_none()
                if ws and getattr(ws, "salt_hex", None):
                    salt = bytes.fromhex(ws.salt_hex)
                else:
                    salt = b"0123456789abcdef0123456789abcdef"
                ws_cache[ws_id] = salt
            salt = ws_cache[ws_id]

            # Determine role hint
            role_hint = "user"
            sender_lower = real_sender.lower()
            if any(k in sender_lower for k in ["客服", "官方", "助手", "技术支持", "support", "liberlive"]):
                role_hint = "support"

            # Get or create real participant
            real_part = await repo.get_or_create_participant(
                workspace_id=ws_id,
                workspace_salt=salt,
                display_name=real_sender,
                source_participant_id=None,
                role_hint=role_hint,
            )
            created_participants.add(real_part.id)

            # Update message
            if not dry_run:
                msg.participant_id = real_part.id
                msg.raw_text = clean_body

            migrated_count += 1

        if not dry_run:
            await session.commit()
            logger.info(f"Successfully migrated {migrated_count} messages to real participants.")

            # 4. Clean up orphaned unknown participants
            for u_part in unknown_parts:
                count_stmt = select(func.count(Message.id)).where(Message.participant_id == u_part.id)
                remaining = (await session.execute(count_stmt)).scalar()
                if remaining == 0:
                    logger.info(f"Removing orphaned unknown participant: {u_part.id} (display_label='{u_part.display_label}')")
                    await session.execute(delete(Participant).where(Participant.id == u_part.id))
            await session.commit()
        else:
            logger.info(f"[DRY RUN] Would migrate {migrated_count} messages across {len(created_participants)} distinct participants.")

        return migrated_count, len(created_participants)


if __name__ == "__main__":
    asyncio.run(repair_unknown_messages(dry_run=False))

import os
import pytest
from pathlib import Path
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from packages.importers.batch_importer import BatchImporter
from packages.importers.scanner import DirectoryScanner
from packages.persistence.db import Base
from packages.persistence.models import (
    Conversation,
    MediaAsset,
    Message,
    MessageMediaLink,
    Participant,
    SourceRoot,
)


@pytest.mark.asyncio
async def test_scan_and_import_real_latest_chat_data():
    raw_root = "D:/玩家群聊天信息2"
    if not Path(raw_root).exists():
        pytest.skip(f"Real data directory {raw_root} not found on this machine")

    scanner = DirectoryScanner()
    # Scan latest date: 20260820
    report, batches = scanner.scan_root(raw_root, target_date="20260820")

    assert len(batches) > 0
    assert report.summary.conversation_count == len(batches)
    assert report.summary.message_drafts > 0
    assert len(report.blocking_issues) == 0

    print(f"\n[Test] Scanned {len(batches)} groups from 20260820:")
    print(f"       Total Messages: {report.summary.message_drafts}")
    print(f"       Total Media Files: {report.summary.media_files}")
    print(f"       Explicit Media Links: {report.summary.explicit_media_links}")
    print(f"       Ambiguous Placeholders: {report.summary.unresolved_image_placeholders}")

    # Set up in-memory sqlite test database
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine) as session:
        importer = BatchImporter(session)

        # 1. First Import Run
        total_inserted_msgs = 0
        total_inserted_media = 0
        for batch in batches:
            stats = await importer.import_parsed_batch(batch, root_path=raw_root)
            assert stats.already_completed is False
            total_inserted_msgs += stats.messages_inserted
            total_inserted_media += stats.media_inserted

        await session.commit()

        # Verify DB counts
        msg_count_res = await session.execute(select(func.count(Message.id)))
        assert msg_count_res.scalar() == total_inserted_msgs

        conv_count_res = await session.execute(select(func.count(Conversation.id)))
        assert conv_count_res.scalar() == len(batches)

        participant_count_res = await session.execute(select(func.count(Participant.id)))
        assert participant_count_res.scalar() > 0

        # Verify participant labels are generated correctly
        sample_p = (await session.execute(select(Participant).limit(1))).scalar_one()
        assert len(sample_p.display_label) > 0

        # 2. Re-import Idempotency Test (Zero duplicates)
        for batch in batches:
            re_stats = await importer.import_parsed_batch(batch, root_path=raw_root)
            assert re_stats.already_completed is True

        await session.commit()

        # Assert no duplicate messages or conversations were created
        msg_count_after = (await session.execute(select(func.count(Message.id)))).scalar()
        assert msg_count_after == total_inserted_msgs

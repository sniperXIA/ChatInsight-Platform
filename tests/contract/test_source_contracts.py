import json
from datetime import datetime
from pathlib import Path
from packages.importers.contracts import (
    SourceBatchManifest,
    SourceMedia,
    SourceMessage,
    SourceSender,
)


def test_batch_manifest_contract():
    manifest = SourceBatchManifest(
        schema_version=2,
        batch_id="batch_001",
        source="wechat_archive",
        conversation_external_id="conv_001",
        conversation_name="LiberLive C2 玩家交流群1",
        exported_at=datetime.now(),
    )
    dumped = manifest.model_dump(mode="json")
    assert dumped["schema_version"] == 2
    assert dumped["batch_id"] == "batch_001"
    assert dumped["conversation_name"] == "LiberLive C2 玩家交流群1"


def test_source_message_contract():
    msg = SourceMessage(
        schema_version=2,
        source_message_id="msg_001",
        sequence=1,
        sent_at=datetime.now(),
        sender=SourceSender(display_name="测试用户"),
        text="这是一条测试消息",
    )
    dumped = msg.model_dump(mode="json")
    assert dumped["source_message_id"] == "msg_001"
    assert dumped["sender"]["display_name"] == "测试用户"


def test_source_media_relative_path_safety():
    # Valid relative path
    media = SourceMedia(
        schema_version=2,
        media_external_id="med_1",
        source_message_id="msg_1",
        order=1,
        kind="image",
        relative_path="images/photo.jpg",
        sha256="abc123",
        size_bytes=100,
    )
    assert media.relative_path == "images/photo.jpg"

    # Invalid path traversal attempt
    import pytest

    with pytest.raises(ValueError):
        SourceMedia(
            schema_version=2,
            media_external_id="med_2",
            source_message_id="msg_1",
            order=1,
            kind="image",
            relative_path="../outside/photo.jpg",
            sha256="abc123",
            size_bytes=100,
        )

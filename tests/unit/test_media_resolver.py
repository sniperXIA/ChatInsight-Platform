from datetime import datetime
from pathlib import Path
from packages.domain.enums import MediaKind, MediaLinkState
from packages.importers.contracts import SourceMedia, SourceMessage, SourceSender
from packages.importers.media_resolver import MediaResolver


def test_explicit_filename_resolution(tmp_path):
    resolver = MediaResolver()

    # Create dummy video media item
    media_item = SourceMedia(
        schema_version=2,
        media_external_id="med_12345",
        kind=MediaKind.VIDEO,
        relative_path="images/video1.mp4",
        sha256="1234567890abcdef",
        size_bytes=1024,
    )

    msg = SourceMessage(
        schema_version=2,
        source_message_id="msg_1",
        sequence=1,
        sent_at=datetime.now(),
        sender=SourceSender(display_name="张三"),
        text="请看这个演示 [video1.mp4]",
        raw_payload_json={"extracted_media_names": ["video1.mp4"], "has_image_placeholder": False},
    )

    links, _ = resolver.resolve_links([msg], [media_item], tmp_path)
    assert len(links) == 1
    assert links[0].method == "explicit_filename"
    assert links[0].confidence == 1.0
    assert links[0].state == MediaLinkState.CONFIRMED
    assert len(msg.media_refs) == 1


def test_ambiguous_image_placeholder_resolution(tmp_path):
    resolver = MediaResolver()

    img1 = SourceMedia(
        schema_version=2,
        media_external_id="med_img1",
        kind=MediaKind.IMAGE,
        relative_path="images/photo1.jpg",
        sha256="aaaabbbb",
        size_bytes=2048,
    )
    img2 = SourceMedia(
        schema_version=2,
        media_external_id="med_img2",
        kind=MediaKind.IMAGE,
        relative_path="images/photo2.jpg",
        sha256="ccccdddd",
        size_bytes=3072,
    )

    msg = SourceMessage(
        schema_version=2,
        source_message_id="msg_placeholder",
        sequence=1,
        sent_at=datetime.now(),
        sender=SourceSender(display_name="李四"),
        text="[图片]",
        raw_payload_json={"extracted_media_names": [], "has_image_placeholder": True},
    )

    # 1 placeholder but 2 unlinked images -> ambiguous candidates
    links, _ = resolver.resolve_links([msg], [img1, img2], tmp_path)
    assert len(links) == 2
    for l in links:
        assert l.state == MediaLinkState.NEEDS_REVIEW
        assert l.confidence < 0.9

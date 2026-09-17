import pytest
from datetime import datetime
from packages.importers.legacy_parser import LegacyTxtParser


def test_parse_single_and_multiline_messages():
    parser = LegacyTxtParser()
    sample_text = [
        "【2026-08-20 08:08:14】庄崇云：编曲做的真的好每一首都是花大量时间和人力物力打磨制作的",
        "【2026-08-20 08:09:16】庄崇云：[adff814f849adec972b9c52a05807612.mp4]",
        "【2026-08-20 08:22:07】言行一致：多行消息第一行",
        "这是多行消息第二行",
        "这是多行消息第三行",
        "【2026-08-20 09:28:48】wxid_t0nnbm115h5q21：@庄崇云 亲，请问支持扩展卡吗？[引用]",
    ]

    drafts, unattached = parser.parse_lines(sample_text)
    assert len(unattached) == 0
    assert len(drafts) == 4

    # First message
    assert drafts[0].sender_text == "庄崇云"
    assert drafts[0].timestamp_text == "2026-08-20 08:08:14"
    assert drafts[0].source_line_start == 1
    assert drafts[0].source_line_end == 1

    # Second message with explicit video
    assert drafts[1].extracted_media_names == ["adff814f849adec972b9c52a05807612.mp4"]

    # Third message (multiline)
    assert drafts[2].source_line_start == 3
    assert drafts[2].source_line_end == 5
    assert len(drafts[2].body_lines) == 3
    assert "多行消息第一行\n这是多行消息第二行\n这是多行消息第三行" == drafts[2].raw_text

    # Fourth message (mention & quote)
    assert "庄崇云" in drafts[3].mentions
    assert drafts[3].extracted_quotes == ["[引用]"]


def test_parse_leading_unattached_lines():
    parser = LegacyTxtParser()
    sample_text = [
        "群导出记录头部说明",
        "==================",
        "【2026-08-20 10:00:00】张三：正常消息",
    ]
    drafts, unattached = parser.parse_lines(sample_text)
    assert len(unattached) == 2
    assert unattached[0].line_number == 1
    assert unattached[0].raw_text == "群导出记录头部说明"
    assert len(drafts) == 1
    assert drafts[0].sender_text == "张三"


def test_convert_drafts_to_messages_with_quotes():
    parser = LegacyTxtParser()
    sample_text = [
        "【2026-08-20 10:00:00】张三：你好，请问有教程吗",
        "【2026-08-20 10:01:00】客服小助手：[引用] @张三 可以查看公众号新手专区",
    ]
    drafts, _ = parser.parse_lines(sample_text)
    messages = parser.convert_drafts_to_messages(
        drafts=drafts,
        source_file_sha256="dummy_sha256",
        conversation_external_id="conv_1",
    )

    assert len(messages) == 2
    assert messages[0].sender.display_name == "张三"
    assert messages[0].sender.role_hint == "user"

    assert messages[1].sender.display_name == "客服小助手"
    assert messages[1].sender.role_hint == "support"
    assert messages[1].quoted_message_id == messages[0].source_message_id


def test_parse_unknown_sender_recovery():
    parser = LegacyTxtParser()
    sample_text = [
        "【2026-04-15 19:52:43】未知：tt8027: 瞎说",
        "【2026-04-15 19:52:48】未知：tt8027: 我就不是",
        "【2026-04-15 20:25:22】未知：qqss8u8: 各位大佬们[社会社会]好呀",
        "【2026-04-15 20:26:00】unknown: 小明: 这是英文unknown的测试",
    ]
    drafts, unattached = parser.parse_lines(sample_text)
    assert len(unattached) == 0
    assert len(drafts) == 4

    assert drafts[0].sender_text == "tt8027"
    assert drafts[0].raw_text == "瞎说"

    assert drafts[1].sender_text == "tt8027"
    assert drafts[1].raw_text == "我就不是"

    assert drafts[2].sender_text == "qqss8u8"
    assert drafts[2].raw_text == "各位大佬们[社会社会]好呀"

    assert drafts[3].sender_text == "小明"
    assert drafts[3].raw_text == "这是英文unknown的测试"

import pytest
import pytest_asyncio
from datetime import datetime
from packages.insights.contracts import EpisodeContextItem, EpisodeContextPacket
from packages.insights.insight_extractor import InsightExtractor
from packages.domain.enums import InsightType


def test_detect_device_model_from_message_text():
    # 1. C2 in message
    packet_c2 = EpisodeContextPacket(
        episode_id="ep_1",
        conversation_id="conv_1",
        conversation_name="LiberLive 交流群",
        title="测试",
        topic_summary="测试",
        category_hint="general",
        started_at="2026-09-01T10:00:00",
        ended_at="2026-09-01T10:10:00",
        messages=[
            EpisodeContextItem(
                message_id="m1",
                sequence=1,
                sent_at="2026-09-01 10:00:00",
                sender_label="UserA",
                sender_role="user",
                raw_text="嗯，C2昨晚生成的三个曲谱都没有自动添加鼓机",
            )
        ],
    )
    assert InsightExtractor._detect_device_model(packet_c2) == "C2"

    # 2. U1 in message
    packet_u1 = EpisodeContextPacket(
        episode_id="ep_2",
        conversation_id="conv_2",
        conversation_name="LiberLive 交流群",
        title="测试",
        topic_summary="测试",
        category_hint="general",
        started_at="2026-09-01T10:00:00",
        ended_at="2026-09-01T10:10:00",
        messages=[
            EpisodeContextItem(
                message_id="m2",
                sequence=1,
                sent_at="2026-09-01 10:00:00",
                sender_label="UserB",
                sender_role="user",
                raw_text="小U音色在升级之后变得更厚实了",
            )
        ],
    )
    assert InsightExtractor._detect_device_model(packet_u1) == "U1"

    # 3. U1 to C2 trade-in -> C2
    packet_tradein = EpisodeContextPacket(
        episode_id="ep_3",
        conversation_id="conv_3",
        conversation_name="LiberLive 交流群",
        title="测试",
        topic_summary="测试",
        category_hint="general",
        started_at="2026-09-01T10:00:00",
        ended_at="2026-09-01T10:10:00",
        messages=[
            EpisodeContextItem(
                message_id="m3",
                sequence=1,
                sent_at="2026-09-01 10:00:00",
                sender_label="UserC",
                sender_role="user",
                raw_text="官方支持U1置换C2以旧换新抵扣吗？",
            )
        ],
    )
    assert InsightExtractor._detect_device_model(packet_tradein) == "C2"

    # 4. Pure general software inquiry without hardware reference -> None
    packet_general = EpisodeContextPacket(
        episode_id="ep_4",
        conversation_id="conv_4",
        conversation_name="LiberLive 综合讨论群",
        title="测试",
        topic_summary="测试",
        category_hint="general",
        started_at="2026-09-01T10:00:00",
        ended_at="2026-09-01T10:10:00",
        messages=[
            EpisodeContextItem(
                message_id="m4",
                sequence=1,
                sent_at="2026-09-01 10:00:00",
                sender_label="UserD",
                sender_role="user",
                raw_text="请问顺丰发货单号一般在哪里查看？",
            )
        ],
    )
    assert InsightExtractor._detect_device_model(packet_general) is None


def test_detect_device_model_from_conversation_name():
    # C2 Group discussing pick hardware
    packet_c2_group = EpisodeContextPacket(
        episode_id="ep_5",
        conversation_id="conv_5",
        conversation_name="LiberLive C2 玩家交流群1",
        title="多轨风格包仅支持B拨片弹奏",
        topic_summary="测试",
        category_hint="general",
        started_at="2026-09-01T10:00:00",
        ended_at="2026-09-01T10:10:00",
        messages=[
            EpisodeContextItem(
                message_id="m5",
                sequence=1,
                sent_at="2026-09-01 10:00:00",
                sender_label="何意味",
                sender_role="user",
                raw_text="多档风格包居然。用a玻片不行",
            ),
            EpisodeContextItem(
                message_id="m6",
                sequence=2,
                sent_at="2026-09-01 10:01:00",
                sender_label="wxid_dyb8xu9z0u2s22",
                sender_role="user",
                raw_text="多档风格包只能用B拨片弹奏，A拨片是换",
            ),
        ],
    )
    assert InsightExtractor._detect_device_model(packet_c2_group) == "C2"

    # U1 Group discussing sound card
    packet_u1_group = EpisodeContextPacket(
        episode_id="ep_6",
        conversation_id="conv_6",
        conversation_name="LiberLive U1玩家交流群 01",
        title="音色扩展卡支持列表",
        topic_summary="测试",
        category_hint="general",
        started_at="2026-09-01T10:00:00",
        ended_at="2026-09-01T10:10:00",
        messages=[
            EpisodeContextItem(
                message_id="m7",
                sequence=1,
                sent_at="2026-09-01 10:00:00",
                sender_label="琴友1",
                sender_role="user",
                raw_text="这个扩展音色卡插槽怎么用？",
            )
        ],
    )
    assert InsightExtractor._detect_device_model(packet_u1_group) == "U1"


def test_clean_svo_summary_strips_boilerplate_prefixes():
    # 1. Strip '用户反馈...'
    raw_1 = "用户反馈物理拨片弹簧过硬导致快速扫弦手指疲劳 (多轨风格包仅支持B拨片弹奏)"
    cleaned_1 = InsightExtractor._clean_svo_summary(raw_1)
    assert "用户反馈" not in cleaned_1
    assert "(" not in cleaned_1 and ")" not in cleaned_1
    assert cleaned_1 == "物理拨片按压偏硬，快速扫弦容易手指疲劳"

    # 2. Strip '用户建议...'
    raw_2 = "用户建议曲库扩充热门流行新歌与经典弹唱曲目 (AI生成曲谱风格呆板且未自动)"
    cleaned_2 = InsightExtractor._clean_svo_summary(raw_2)
    assert "用户建议" not in cleaned_2
    assert "(" not in cleaned_2 and ")" not in cleaned_2
    assert cleaned_2 == "热门流行新歌与经典弹唱曲目收录较少，建议持续扩充"

    # 3. Strip '琴友在社群中积极分享便携易上手弹唱体验并给予好评'
    raw_3 = "琴友在社群中积极分享便携易上手弹唱体验并给予好评 (鼓机模式进入与长按关闭操作卡)"
    cleaned_3 = InsightExtractor._clean_svo_summary(raw_3)
    assert "琴友在社群中" not in cleaned_3
    assert "(" not in cleaned_3 and ")" not in cleaned_3
    assert cleaned_3 == "琴身便携易上手设计与伴奏弹唱体验获得好评"


def test_heuristic_extraction_badcase_1_style_pack_and_picks():
    extractor = InsightExtractor(session=None, model_gateway=None)
    packet = EpisodeContextPacket(
        episode_id="048228aa-759f-41a0-9b3b-a4a820d89940",
        conversation_id="conv_c2_1",
        conversation_name="LiberLive C2 玩家交流群1",
        title="多轨风格包仅支持B拨片弹奏",
        topic_summary="测试",
        category_hint="general",
        started_at="2026-09-01T21:02:12",
        ended_at="2026-09-01T21:03:53",
        messages=[
            EpisodeContextItem(
                message_id="m_1",
                sequence=1,
                sent_at="2026-09-01 21:02:12",
                sender_label="何意味",
                sender_role="user",
                raw_text="多档风格包居然。用a玻片不行",
            ),
            EpisodeContextItem(
                message_id="m_2",
                sequence=2,
                sent_at="2026-09-01 21:03:34",
                sender_label="wxid_dyb8xu9z0u2s22",
                sender_role="user",
                raw_text="多档风格包只能用B拨片弹奏，A拨片是换",
            ),
            EpisodeContextItem(
                message_id="m_3",
                sequence=3,
                sent_at="2026-09-01 21:03:47",
                sender_label="何意味",
                sender_role="user",
                raw_text="[捂脸] 好吧",
            ),
        ],
    )

    batch_out = extractor._generate_dynamic_heuristic_insight(packet)
    assert len(batch_out.insights) == 1
    ins = batch_out.insights[0]
    # SVO standard: function/object + specific limitation + complement
    assert ins.summary == "多轨风格包仅支持B拨片弹奏，使用不便"
    assert "用户反馈" not in ins.summary
    assert "(" not in ins.summary and ")" not in ins.summary
    assert ins.device_model == "C2"
    assert ins.insight_type == InsightType.ISSUE


def test_heuristic_extraction_badcase_2_ai_sheet_no_drum_machine():
    extractor = InsightExtractor(session=None, model_gateway=None)
    packet = EpisodeContextPacket(
        episode_id="fac963b0-3dbc-4de7-a3c3-f5e48e3d932b",
        conversation_id="conv_c2_4",
        conversation_name="LiberLive C2玩家交流群 4",
        title="AI生成曲谱风格呆板且未自动添加鼓机",
        topic_summary="测试",
        category_hint="general",
        started_at="2026-09-01T10:11:37",
        ended_at="2026-09-01T10:29:04",
        messages=[
            EpisodeContextItem(
                message_id="m_20",
                sequence=1,
                sent_at="2026-09-01 10:11:37",
                sender_label="潴、寳。",
                sender_role="user",
                raw_text="AI生成的曲谱太呆板了",
            ),
            EpisodeContextItem(
                message_id="m_21",
                sequence=2,
                sent_at="2026-09-01 10:22:48",
                sender_label="King",
                sender_role="user",
                raw_text="ai生成的曲谱没有自动添加鼓机",
            ),
            EpisodeContextItem(
                message_id="m_22",
                sequence=3,
                sent_at="2026-09-01 10:27:59",
                sender_label="林",
                sender_role="user",
                raw_text="C2吗？@King",
            ),
            EpisodeContextItem(
                message_id="m_23",
                sequence=4,
                sent_at="2026-09-01 10:29:04",
                sender_label="King",
                sender_role="user",
                raw_text="嗯，C2昨晚生成的三个曲谱都没有自动添加鼓机",
            ),
        ],
    )

    batch_out = extractor._generate_dynamic_heuristic_insight(packet)
    assert len(batch_out.insights) == 1
    ins = batch_out.insights[0]
    # SVO standard: AI生成曲谱 + 没有自动鼓机 + 演奏呆板不生动
    assert ins.summary == "AI生成曲谱没有自动鼓机，演奏呆板不生动"
    assert "用户建议" not in ins.summary
    assert "(" not in ins.summary and ")" not in ins.summary
    assert ins.device_model == "C2"
    assert ins.insight_type == InsightType.ISSUE


@pytest.mark.asyncio
async def test_device_model_filtering_in_repository_and_api():
    from httpx import AsyncClient, ASGITransport
    from apps.api.main import app
    from packages.persistence.db import get_session_context
    from packages.persistence.repositories.registry import RepositoryRegistry

    async with get_session_context() as session:
        repo = RepositoryRegistry(session)

        # Test repository get_insights with device_model
        c2_insights = await repo.get_insights(device_model="C2", limit=10)
        assert len(c2_insights) > 0
        for item in c2_insights:
            assert item.device_model == "C2"

        u1_insights = await repo.get_insights(device_model="U1", limit=10)
        assert len(u1_insights) > 0
        for item in u1_insights:
            assert item.device_model == "U1"

        none_insights = await repo.get_insights(device_model="none", limit=10)
        for item in none_insights:
            assert item.device_model is None or item.device_model == ""

        # Test repository get_insights_for_stats with device_model
        c2_stats_list = await repo.get_insights_for_stats(device_model="C2")
        assert len(c2_stats_list) > 0
        assert all(item.device_model == "C2" for item in c2_stats_list)

    # Test HTTP API endpoints
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # GET /api/v1/insights?device_model=C2
        res_c2 = await ac.get("/api/v1/insights?device_model=C2&limit=20")
        assert res_c2.status_code == 200
        data_c2 = res_c2.json()
        assert len(data_c2) > 0
        assert all(item.get("device_model") == "C2" for item in data_c2)

        # GET /api/v1/insights?device_model=U1
        res_u1 = await ac.get("/api/v1/insights?device_model=U1&limit=20")
        assert res_u1.status_code == 200
        data_u1 = res_u1.json()
        assert len(data_u1) > 0
        assert all(item.get("device_model") == "U1" for item in data_u1)

        # GET /api/v1/insights/stats?device_model=C2
        stats_c2 = await ac.get("/api/v1/insights/stats?device_model=C2")
        assert stats_c2.status_code == 200
        s_data = stats_c2.json()
        assert s_data["total_insights"] > 0


def test_clean_svo_summary_strips_boilerplate_suffixes_comprehensive():
    extractor = InsightExtractor(session=None, model_gateway=None)

    cases = [
        ("伴奏曲谱和弦自定义编辑与第三方乐谱文件导入，使用交流与操作咨询", "伴奏曲谱和弦自定义编辑与第三方乐谱文件导入"),
        ("平板端App曲谱字体调整方法，建议曲库扩充收录", "平板端App曲谱字体调整方法"),
        ("鼓点混乱与强弱拍反调体验，功能体验交流", "鼓点混乱与强弱拍反调体验"),
        ("室内吉他弹唱录音人声过强琴声音量偏弱，偶发异常体验不佳", "室内吉他弹唱录音人声过强琴声音量偏弱"),
        ("美区苹果ID下载海外版应用方法，使用交流与操作咨询", "美区苹果ID下载海外版应用方法"),
        ("伴奏风格包自定义配置与个性化调节，使用交流与操作咨询", "伴奏风格包自定义配置与个性化调节"),
        ("多维曲谱多轨歌曲更新缺失与拓展芯片新谱，建议曲库扩充收录", "多维曲谱多轨歌曲更新缺失与拓展芯片新谱"),
        ("鼓机模式进入与长按关闭操作卡点，偶发异常体验不佳", "鼓机模式进入与长按关闭操作卡点"),
    ]

    for raw, expected in cases:
        cleaned = extractor._clean_svo_summary(raw)
        assert cleaned == expected, f"Expected {expected}, got {cleaned}"
        assert not any(t in cleaned for t in ["使用交流与操作咨询", "建议曲库扩充收录", "功能体验交流", "偶发异常体验不佳"])


def test_heuristic_sheet_font_and_custom_import_not_overgeneralized():
    extractor = InsightExtractor(session=None, model_gateway=None)

    # 1. Sheet font adjustment -> should NOT be categorized into song library expansion
    packet_font = EpisodeContextPacket(
        episode_id="ep_font_1",
        conversation_id="conv_pad_1",
        conversation_name="LiberLive 平板体验交流群",
        title="平板端App曲谱字体调整方法",
        topic_summary="平板端App曲谱字体调整方法",
        category_hint="general",
        started_at="2026-09-01T10:00:00",
        ended_at="2026-09-01T10:10:00",
        messages=[
            EpisodeContextItem(
                message_id="m_font_1",
                sequence=1,
                sent_at="2026-09-01 10:00:00",
                sender_label="海内存知己",
                sender_role="user",
                raw_text="在平板上看曲谱字号太小了看不清，有什么办法把字体调大吗？",
            )
        ],
    )
    res_font = extractor._generate_dynamic_heuristic_insight(packet_font)
    assert len(res_font.insights) == 1
    ins_font = res_font.insights[0]
    assert "建议曲库扩充收录" not in ins_font.summary
    assert "热门流行新歌" not in ins_font.summary
    assert "界面与显示" in ins_font.module
    assert "字体" in ins_font.summary or "字号" in ins_font.summary

    # 2. Custom chord edit & sheet import
    packet_import = EpisodeContextPacket(
        episode_id="ep_imp_1",
        conversation_id="conv_imp_1",
        conversation_name="LiberLive 玩家群",
        title="伴奏曲谱和弦自定义编辑与第三方乐谱文件导入",
        topic_summary="第三方乐谱导入",
        category_hint="general",
        started_at="2026-09-01T10:00:00",
        ended_at="2026-09-01T10:10:00",
        messages=[
            EpisodeContextItem(
                message_id="m_imp_1",
                sequence=1,
                sent_at="2026-09-01 10:00:00",
                sender_label="乐手小张",
                sender_role="user",
                raw_text="支持自己导入本地曲谱或者编辑和弦吗？想导入txt格式的谱子。",
            )
        ],
    )
    res_imp = extractor._generate_dynamic_heuristic_insight(packet_import)
    assert len(res_imp.insights) == 1
    ins_imp = res_imp.insights[0]
    assert "建议曲库扩充收录" not in ins_imp.summary
    assert "自定义与导入" in (ins_imp.sub_module or "")


def test_life_chitchat_is_filtered_out():
    extractor = InsightExtractor(session=None, model_gateway=None)

    chitchat_texts = [
        "人越来越少了",
        "庐山路的银杏着实不错，有思想才能有作品",
        "为什么9月份要分别呢？",
        "这车也太帅了",
    ]

    for txt in chitchat_texts:
        packet = EpisodeContextPacket(
            episode_id="ep_chitchat",
            conversation_id="conv_chitchat",
            conversation_name="综合大群",
            title=txt,
            topic_summary=txt,
            category_hint="general",
            started_at="2026-09-01T10:00:00",
            ended_at="2026-09-01T10:10:00",
            messages=[
                EpisodeContextItem(
                    message_id="m_chat",
                    sequence=1,
                    sent_at="2026-09-01 10:00:00",
                    sender_label="路人",
                    sender_role="user",
                    raw_text=txt,
                )
            ],
        )
        res = extractor._generate_dynamic_heuristic_insight(packet)
        assert len(res.insights) == 0, f"Expected 0 insights for chitchat '{txt}', got {len(res.insights)}"



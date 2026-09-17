import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import app
from packages.insights.episode_deduplicator import DistinctTopicCluster, EpisodeDeduplicator, EpisodeItemView
from packages.insights.tag_manager import TagDefinition, TagManager
from packages.persistence.db import Base, get_session


def test_tag_manager_hierarchical_structure():
    TagManager.clear_cache()
    all_tags = TagManager.get_all_tags()
    l1_tags = TagManager.get_level1_tags()
    l2_tags = TagManager.get_level2_tags()

    assert len(all_tags) >= 40
    assert len(l1_tags) >= 13
    assert len(l2_tags) >= 25

    # Check ui_ux and its subcategories
    ui_ux = TagManager.get_tag_info("ui_ux")
    assert ui_ux.name_zh == "界面与显示"
    assert ui_ux.level == 1

    ui_font = TagManager.get_tag_info("ui_font")
    assert ui_font.name_zh == "字体与清晰度"
    assert ui_font.parent_key == "ui_ux"
    assert ui_font.level == 2
    assert "看不清" in ui_font.keywords

    # Check display name rendering
    assert TagManager.get_tag_display_name("ui_font") == "界面与显示 · 字体与清晰度"
    assert TagManager.get_tag_display_name("sheet_report") == "曲谱与乐库 · 曲谱报错纠错"


def test_tag_manager_semantic_matching_for_ui_and_subcategories():
    # 1. Test "我早就看不清楚了" specifically requested by user
    l1, l2, display = TagManager.match_best_tag("我早就看不清楚了，就靠摸索")
    assert l1 == "ui_ux"
    assert l2 == "ui_font"
    assert "界面与显示" in display

    # 2. Test other UI complaints
    l1, l2, _ = TagManager.match_best_tag("这个App的字太小了，眼睛都快看瞎了")
    assert l1 == "ui_ux"
    assert l2 == "ui_font"

    l1, l2, _ = TagManager.match_best_tag("希望能出个暗黑皮肤或者深色模式，晚上太亮了刺眼")
    assert l1 == "ui_ux"
    assert l2 == "ui_theme"

    l1, l2, _ = TagManager.match_best_tag("横屏的时候按键把谱面遮挡错位了")
    assert l1 == "ui_ux"
    assert l2 == "ui_layout"

    # 3. Test sheet music error & requests
    l1, l2, _ = TagManager.match_best_tag("晴天这首歌第3小节的和弦标错了")
    assert l1 == "sheet_music"
    assert l2 == "sheet_report"

    l1, l2, _ = TagManager.match_best_tag("官方乐库能不能加一首周杰伦的新歌")
    assert l1 == "sheet_music"
    assert l2 == "sheet_request"

    # 4. Test performance & tracking
    l1, l2, _ = TagManager.match_best_tag("弹唱的时候走带老是卡顿跳音跟不上谱")
    assert l1 == "performance"
    assert l2 == "play_track"

    # 5. Test bluetooth latency
    l1, l2, _ = TagManager.match_best_tag("伴奏通过蓝牙播放声音慢半拍有延迟")
    assert l1 == "bluetooth_conn"
    assert l2 == "ble_audio"

    # 6. Test device compatibility (HarmonyOS & iPad)
    l1, l2, _ = TagManager.match_best_tag("华为鸿蒙HarmonyOS系统更新后打开闪退")
    assert l1 == "device_compat"
    assert l2 == "compat_harmony"

    l1, l2, _ = TagManager.match_best_tag("iPad横屏排版黑边比例不对")
    assert l1 == "device_compat"
    assert l2 == "compat_pad"

    # 7. Test hardware & U1
    l1, l2, _ = TagManager.match_best_tag("拨片按键手感太生硬了")
    assert l1 == "hardware_craft"
    assert l2 == "hw_buttons_picks"

    l1, l2, _ = TagManager.match_best_tag("U1新品吉他什么时候上市发售排期几月")
    assert l1 == "new_product"
    assert l2 == "np_schedule"


def test_distinct_clustering_for_ui_and_excel_categories():
    episodes = [
        EpisodeItemView(
            id="ep_ui_1",
            conversation_id="conv_1",
            conversation_name="玩家群",
            title="反馈App界面字号偏小、暗黑模式辨识度及谱面显示效果",
            summary="我早就看不清楚了，就靠摸索，希望能放大字体。",
            category_hint="ui_ux",
            started_at="2026-08-27 10:00:00",
            ended_at="2026-08-27 10:05:00",
            message_count=6,
            participants=["琴友小张", "琴友老李"],
        ),
        EpisodeItemView(
            id="ep_sheet_1",
            conversation_id="conv_1",
            conversation_name="玩家群",
            title="反馈官方曲谱和弦标错、歌词错漏与节奏标记不准",
            summary="晴天和弦标错了，建议官方纠错更新。",
            category_hint="sheet_music",
            started_at="2026-08-27 10:10:00",
            ended_at="2026-08-27 10:15:00",
            message_count=4,
            participants=["琴友王五"],
        ),
        EpisodeItemView(
            id="ep_harmony_1",
            conversation_id="conv_1",
            conversation_name="玩家群",
            title="反馈华为鸿蒙系统运行兼容性与跨版本系统闪退",
            summary="鸿蒙next系统更新后打开直接闪退。",
            category_hint="device_compat",
            started_at="2026-08-27 10:20:00",
            ended_at="2026-08-27 10:25:00",
            message_count=5,
            participants=["琴友刘六"],
        ),
    ]

    clusters = EpisodeDeduplicator.deduplicate_episodes(episodes)
    assert len(clusters) == 3

    categories = {c.category_hint for c in clusters}
    assert "ui_ux" in categories
    assert "sheet_music" in categories
    assert "device_compat" in categories
    assert "general" not in categories

    ui_cluster = next(c for c in clusters if c.category_hint == "ui_ux")
    assert "界面与显示" in ui_cluster.canonical_title
    assert "字号偏小" in ui_cluster.canonical_title or "看不清" in ui_cluster.canonical_title


@pytest.mark.asyncio
async def test_tags_api_full_hierarchy(tmp_path):
    test_db_path = tmp_path / "test_tags_hier.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{test_db_path}", echo=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.get("/api/v1/tags")
        assert res.status_code == 200
        tags = res.json()
        assert len(tags) >= 40

        # Verify level and parent_key fields
        ui_font_tag = next((t for t in tags if t["key"] == "ui_font"), None)
        assert ui_font_tag is not None
        assert ui_font_tag["level"] == 2
        assert ui_font_tag["parent_key"] == "ui_ux"
        assert "bug" in ui_font_tag["issue_types"]
        assert "看不清" in ui_font_tag["keywords"]

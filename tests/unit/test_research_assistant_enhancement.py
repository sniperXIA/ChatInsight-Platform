import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from packages.persistence.db import get_session_context
from packages.search.contracts import Citation, RequirementItem, SearchFilter
from packages.search.history_service import ResearchHistoryService
from packages.search.research_assistant import (
    ResearchAssistant,
    format_problem_statement,
    format_recommended_actions,
    parse_5w1h_dict,
    parse_fallback_requirements,
)


def test_parse_5w1h_dict():
    sample_text = (
        "[场景/位置] 户外野餐或蓝牙音响旁 [具体现象] 手机与电钢琴无法搜到彼此蓝牙信号，重试多次无果；"
        "[影响程度] 无法进行伴奏练习与录音 [环境/触发条件] 蓝牙周边设备较多时 "
        "[社群进展] 官方客服建议升级最新固件补丁。"
    )
    res = parse_5w1h_dict(sample_text)
    assert res.get("scene") == "户外野餐或蓝牙音响旁"
    assert res.get("symptom") == "手机与电钢琴无法搜到彼此蓝牙信号，重试多次无果"
    assert res.get("impact") == "无法进行伴奏练习与录音"
    assert res.get("condition") == "蓝牙周边设备较多时"
    assert res.get("progress") == "官方客服建议升级最新固件补丁"


def test_parse_fallback_requirements():
    sample_answer = """
基于社群讨论，主要存在以下体验问题：
1. 蓝牙无线配对连接延迟与断连 [1]
用户在复杂无线环境下连接设备耗时过长，且偶现断开连接。
2. 曲谱夜间深色模式缺失 [2]
夜间室内练琴时屏幕刺眼，对比度不足。
    """
    reqs = parse_fallback_requirements(sample_answer)
    assert len(reqs) == 2
    assert reqs[0].req_id == "REQ-1"
    assert "蓝牙" in reqs[0].title
    assert 1 in reqs[0].citation_ids
    assert reqs[1].req_id == "REQ-2"
    assert "深色模式" in reqs[1].title
    assert 2 in reqs[1].citation_ids


@pytest.mark.asyncio
async def test_research_history_service_crud_and_prune():
    async with get_session_context() as session:
        service = ResearchHistoryService(session)

        # Clear existing test data
        await service.clear_all_history()
        await session.commit()

        # 1. Save records
        r1 = await service.save_record(
            workspace_id="default",
            question="问题一：关于电钢琴音色卡无法识别",
            answer_text="音色卡接触不良 [1]",
            executive_summary="音色卡金手指氧化问题分析",
            requirements=[
                RequirementItem(
                    req_id="REQ-1",
                    title="音色卡接触不良",
                    category="硬件外设",
                    severity="major",
                    problem_statement="插入后指示灯不亮",
                    recommended_action="重新插拔",
                    citation_ids=[1],
                ).model_dump()
            ],
            citations=[
                Citation(
                    citation_id=1,
                    evidence_uri="chatinsight://test/1",
                    title="音色卡反馈",
                    snippet="插入后指示灯不亮",
                ).model_dump()
            ],
            duration_ms=85.0,
        )
        await session.commit()

        # 2. Find exact cached
        cached = await service.find_exact_cached("问题一：关于电钢琴音色卡无法识别", days=30)
        assert cached is not None
        assert cached.id == r1.id

        # Cached query with days=0 (simulate expired)
        cached_expired = await service.find_exact_cached("问题一：关于电钢琴音色卡无法识别", days=-1)
        assert cached_expired is None

        # 3. List history with search filter
        found_list = await service.list_history(search="音色卡")
        assert len(found_list) == 1
        assert found_list[0].id == r1.id

        empty_list = await service.list_history(search="不存在的关键词xyz")
        assert len(empty_list) == 0

        # 4. Test max_records pruning
        # Save 4 more records
        for i in range(2, 6):
            await service.save_record(
                workspace_id="default",
                question=f"问题{i}",
                answer_text=f"回答{i}",
                requirements=[],
                citations=[],
            )
        await session.commit()

        total_before = len(await service.list_history())
        assert total_before == 5

        # Prune with max_records=3
        pruned_count = await service.prune_expired(retention_days=30, max_records=3)
        await session.commit()
        assert pruned_count == 2

        remaining = await service.list_history()
        assert len(remaining) == 3

        # Clean up
        await service.clear_all_history()
        await session.commit()


@pytest.mark.asyncio
async def test_research_assistant_end_to_end_caching():
    async with get_session_context() as session:
        service = ResearchHistoryService(session)
        await service.clear_all_history()
        await session.commit()

        assistant = ResearchAssistant(session)
        q = "关于蓝牙连接稳定性的体验与优化建议是什么？"

        # 1. First call (not in history -> force_mock generates output & saves to history)
        res1 = await assistant.answer_question(
            question=q,
            filters=SearchFilter(),
            force_mock=True,
            force_refresh=False,
        )
        assert res1.from_history is False
        assert res1.history_id is not None
        assert len(res1.requirements) > 0
        assert res1.executive_summary is not None
        assert len(res1.citations) > 0

        # 2. Second call without force_refresh -> should hit local history!
        res2 = await assistant.answer_question(
            question=q,
            filters=SearchFilter(),
            force_mock=False,
            force_refresh=False,
        )
        assert res2.from_history is True
        assert res2.history_id == res1.history_id
        assert len(res2.requirements) == len(res1.requirements)
        assert res2.requirements[0].req_id == res1.requirements[0].req_id

        # 3. Third call with force_refresh=True -> should bypass history!
        res3 = await assistant.answer_question(
            question=q,
            filters=SearchFilter(),
            force_mock=True,
            force_refresh=True,
        )
        assert res3.from_history is False
        assert res3.history_id is not None

        # Clean up
        await service.clear_all_history()
        await session.commit()


@pytest.mark.asyncio
async def test_assistant_api_routes():
    from httpx import AsyncClient, ASGITransport
    from apps.api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Ask question via API
        res = await ac.post("/api/v1/assistant/ask", json={"question": "用户关于蓝牙连接与断连的问题主要有哪些？", "force_mock": True})
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["question"] == "用户关于蓝牙连接与断连的问题主要有哪些？"
        assert len(data["requirements"]) > 0
        h_id = data["history_id"]
        assert h_id is not None

        # 2. Get history list
        h_res = await ac.get("/api/v1/assistant/history")
        assert h_res.status_code == 200
        items = h_res.json()
        assert any(item["id"] == h_id for item in items)

        # 3. Get history detail
        d_res = await ac.get(f"/api/v1/assistant/history/{h_id}")
        assert d_res.status_code == 200
        assert d_res.json()["history_id"] == h_id

        # 4. Settings
        s_res = await ac.get("/api/v1/assistant/settings")
        assert s_res.status_code == 200

        up_res = await ac.post("/api/v1/assistant/settings", json={"history_retention_days": 45, "history_max_records": 120})
        assert up_res.status_code == 200
        assert up_res.json()["history_retention_days"] == 45

        # 5. Delete history
        del_res = await ac.delete(f"/api/v1/assistant/history/{h_id}")
        assert del_res.status_code == 200

        # Restore settings
        await ac.post("/api/v1/assistant/settings", json={"history_retention_days": 30, "history_max_records": 100})


@pytest.mark.asyncio
async def test_history_service_realtime_timestamp():
    async with get_session_context() as session:
        service = ResearchHistoryService(session)
        now_before = datetime.now()
        rec = await service.save_record(
            workspace_id="default",
            question="测试真实时间间距",
            answer_text="回答内容",
            executive_summary="摘要",
            requirements=[],
            citations=[],
        )
        await session.commit()
        now_after = datetime.now()

        # created_at should be between now_before and now_after, NOT 8 hours off!
        diff_sec = abs((rec.created_at - now_before).total_seconds())
        assert diff_sec < 5.0, f"Timestamp is offset: {diff_sec}s"

        # Cleanup
        await service.delete_record(rec.id)
        await session.commit()


@pytest.mark.asyncio
async def test_topic_to_insight_evidence_drilldown():
    from apps.api.routes.insights import get_insight_evidence
    from packages.persistence.models import Conversation, Episode, Insight, InsightClaim, Topic, TopicInsightLink, Workspace
    from sqlalchemy import select

    async with get_session_context() as session:
        # Find any topic that has a linked insight with claims
        link_stmt = select(TopicInsightLink).limit(1)
        link = (await session.execute(link_stmt)).scalar_one_or_none()
        if not link:
            # Seed a linked topic and insight for drilldown test
            ws = await session.get(Workspace, "default")
            if not ws:
                ws = Workspace(id="default", name="Default Workspace")
                session.add(ws)
                await session.flush()

            conv_stmt = select(Conversation).limit(1)
            conv = (await session.execute(conv_stmt)).scalar_one_or_none()
            if not conv:
                conv = Conversation(workspace_id=ws.id, source_type="test", source_conversation_id="c_test", name="测试群")
                session.add(conv)
                await session.flush()

            ep_stmt = select(Episode).limit(1)
            ep = (await session.execute(ep_stmt)).scalar_one_or_none()
            if not ep:
                ep = Episode(workspace_id=ws.id, conversation_id=conv.id, title="测试片段", message_count=1)
                session.add(ep)
                await session.flush()

            ins = Insight(
                workspace_id=ws.id,
                episode_id=ep.id,
                insight_type="issue",
                module="界面与显示",
                summary="App增加黑白背景主题切换",
                description="用户建议增加黑白背景切换以提升可读性",
            )
            session.add(ins)
            await session.flush()

            claim = InsightClaim(
                insight_id=ins.id,
                claim_key="ui_theme_contrast",
                claim_text="用户反映浅色高亮背景在夜间刺眼",
                evidence_uris_json=["chatinsight://conv/1/msg_1#text"],
            )
            session.add(claim)

            topic = Topic(
                workspace_id=ws.id,
                title="增加黑白背景主题切换",
                summary="中老年用户建议App增加黑白背景主题切换功能以提升可读性",
                module="界面与显示",
            )
            session.add(topic)
            await session.flush()

            link = TopicInsightLink(topic_id=topic.id, insight_id=ins.id)
            session.add(link)
            await session.commit()
            target_topic_id = topic.id
        else:
            target_topic_id = link.topic_id

        # Querying evidence using Topic ID should automatically resolve without 404
        evidence_resp = await get_insight_evidence(target_topic_id, session)
        assert evidence_resp is not None
        assert evidence_resp.insight_id is not None
        assert len(evidence_resp.claims) > 0


@pytest.mark.asyncio
async def test_hybrid_search_voc_filtering_and_taxonomy_boosting():
    from packages.search.hybrid_search import HybridSearchEngine
    async with get_session_context() as session:
        engine = HybridSearchEngine(session)
        res = await engine.search("目前UI层面用户反映的问题主要有哪些？", limit=6)
        assert len(res.results) > 0
        # All top results should be UI/UX or display related, none from unrelated hardware sound/battery
        for r in res.results:
            mod = (r.metadata.get("module") or "").lower()
            assert any(term in mod for term in ["ui", "界面", "显示", "字体", "深色", "排版", "综合体验"]), f"Unrelated module: {mod}"


@pytest.mark.asyncio
async def test_repertoire_song_query_relevance_and_noise_filtering():
    from packages.search.hybrid_search import HybridSearchEngine
    async with get_session_context() as session:
        engine = HybridSearchEngine(session)
        res = await engine.search("产品曲库和歌曲资源方面，用户有哪些问题或建议？", limit=8)
        assert len(res.results) > 0
        for r in res.results:
            mod = (r.metadata.get("module") or "").lower()
            tit = r.title.lower()
            snip = r.snippet.lower()
            # Must be repertoire, sheet music, or style pack related - NO hardware U1/C2 replacement or mic bracket
            is_repertoire = any(k in mod or k in tit or k in snip for k in ["曲", "歌", "谱", "乐库", "风格包"])
            assert is_repertoire, f"Irrelevant item found in repertoire query: {r.title} ({mod})"
            assert "u1换c2" not in tit and "支架" not in tit and "和弦区别" not in tit


@pytest.mark.asyncio
async def test_zero_recall_fast_path():
    from packages.search.research_assistant import ResearchAssistant
    async with get_session_context() as session:
        assistant = ResearchAssistant(session)
        # Query on something totally absent from the knowledge base
        res = await assistant.answer_question("用户是否有反馈过车载CarPlay无线投屏的死机与丢帧问题？", force_refresh=True)
        assert len(res.requirements) == 0
        assert len(res.citations) == 0
        assert "暂未发现" in res.executive_summary
        assert "暂未发现" in res.answer
        assert res.model_used == "system-direct"


def test_format_problem_statement_and_recommended_actions():
    raw_problem = "• 【问题现象】开启旅行锁后开机扩展卡异常；• 【诱发原因】时序冲突；• 【用户建议】希望先开机再上锁；• 【需求补充】多位用户反馈"
    fmt_p = format_problem_statement(raw_problem)
    assert "• 【问题现象】" in fmt_p
    assert "• 【诱发原因】" in fmt_p
    assert "\n" in fmt_p

    raw_action = "1) 在售卖页明确清单；2) 建立回访机制；3) 持续OTA更新内容"
    fmt_a = format_recommended_actions(raw_action)
    lines = fmt_a.splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("1)")
    assert lines[1].startswith("2)")
    assert lines[2].startswith("3)")


@pytest.mark.asyncio
async def test_message_entity_drilldown_evidence():
    from apps.api.routes.insights import get_insight_evidence
    from packages.persistence.models import Message
    from sqlalchemy import select

    async with get_session_context() as session:
        msg = (await session.execute(select(Message).limit(1))).scalar_one_or_none()
        if msg:
            evidence_resp = await get_insight_evidence(msg.id, session)
            assert evidence_resp is not None
            assert evidence_resp.insight_id is not None and len(evidence_resp.insight_id) > 0
            assert len(evidence_resp.claims) > 0
            assert len(evidence_resp.messages) > 0


def test_request_timeout_configuration():
    from packages.model_gateway.gateway import ModelGateway
    from packages.model_gateway.openrouter_adapter import OpenRouterAdapter
    from packages.model_gateway.settings_manager import SettingsManager

    settings = SettingsManager.get_settings(reload=True)
    assert settings.request_timeout_seconds == 240.0
    assert settings.research_assistant.reasoning_effort == "medium"
    assert settings.research_assistant.thinking_budget == 2048

    adapter = OpenRouterAdapter(api_key="test_key", timeout_seconds=settings.request_timeout_seconds)
    assert adapter.timeout == 240.0

    gateway = ModelGateway()
    provider = gateway.get_text_provider()
    assert getattr(provider, "timeout", None) == 240.0

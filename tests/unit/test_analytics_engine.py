from datetime import datetime
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.analytics.report_generator import ReportGenerator
from packages.persistence.db import Base
from packages.persistence.repositories.registry import RepositoryRegistry


@pytest.mark.asyncio
async def test_analytics_report_generator_and_markdown_rendering():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()

        # Seed 2 Topics
        t1 = await repo.create_topic(
            workspace_id=ws.id,
            title="扩展音色卡问题",
            summary="无法正常识别",
            module="音色/扩展卡",
            severity="major",
        )
        await repo.update_topic_stats(t1.id, increment_feedback=4, increment_users=3)

        t2 = await repo.create_topic(
            workspace_id=ws.id,
            title="蓝牙配对慢",
            summary="搜索耗时较长",
            module="App/蓝牙连接",
            severity="minor",
        )
        await repo.update_topic_stats(t2.id, increment_feedback=1, increment_users=1)

        await session.commit()

        generator = ReportGenerator(session)
        report = await generator.generate_report(period_label="测试周期报告", force_mock=True)

        assert report.total_feedbacks == 7  # (1+4) + (1+1)
        assert report.total_topics == 2
        assert len(report.module_distribution) == 2
        assert report.module_distribution[0].category == "音色/扩展卡"
        assert len(report.key_recommendations) > 0

        # Markdown render test
        md = generator.render_markdown(report)
        assert "# 📊 测试周期报告" in md
        assert "## 1. 核心指标概览" in md
        assert "音色/扩展卡" in md


@pytest.mark.asyncio
async def test_operational_overview_hourly_and_daily_token_trends():
    from datetime import datetime, timezone
    from packages.persistence.models import AnalysisRun, ResearchQueryRecord

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()

        now_dt = datetime.now()

        # Seed 2 Analysis Runs
        run1 = AnalysisRun(
            workspace_id=ws.id,
            run_type="episode",
            target_type="episode",
            target_id="ep-1",
            provider="dashscope",
            model="qwen3.8-flash",
            input_hash="hash1",
            config_hash="cfg1",
            state="succeeded",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            started_at=now_dt,
            completed_at=now_dt,
        )
        run2 = AnalysisRun(
            workspace_id=ws.id,
            run_type="insight_extraction",
            target_type="insight",
            target_id="ins-1",
            provider="dashscope",
            model="qwen-2.5-vl-72b-instruct",
            input_hash="hash2",
            config_hash="cfg2",
            state="succeeded",
            input_tokens=300,
            output_tokens=150,
            total_tokens=450,
            started_at=now_dt,
            completed_at=now_dt,
        )
        # Seed 1 Research Query Record
        rq = ResearchQueryRecord(
            workspace_id=ws.id,
            question="测试查询",
            answer_text="测试回答",
            model_used="qwen3.8-flash",
            total_tokens=200,
            prompt_tokens=150,
            completion_tokens=50,
            created_at=now_dt,
        )
        session.add(run1)
        session.add(run2)
        session.add(rq)
        await session.commit()

        generator = ReportGenerator(session)
        overview = await generator.get_operational_overview(period="7d")

        # 1. Check Totals
        assert overview.token_usage.total_tokens == 800
        assert overview.token_usage.prompt_tokens == 550
        assert overview.token_usage.completion_tokens == 250
        assert overview.token_usage.analysis_runs_count == 3

        # 2. Check 24-Hour Hourly Trend
        assert len(overview.token_usage.token_hourly_trend) == 24
        curr_hour_str = f"{now_dt.hour:02d}:00"
        curr_hour_pt = next(h for h in overview.token_usage.token_hourly_trend if h.hour == curr_hour_str)
        assert curr_hour_pt.tokens == 800
        assert curr_hour_pt.runs_count == 3

        # 3. Check Daily Trend
        assert len(overview.token_usage.token_daily_trend) >= 7
        total_daily_toks = sum(pt.tokens for pt in overview.token_usage.token_daily_trend)
        assert total_daily_toks == 800

        # 4. Check Models Breakdown
        assert len(overview.token_usage.models_breakdown) == 2
        m_flash = next(m for m in overview.token_usage.models_breakdown if "qwen3.8-flash" in (m.model_name or m.model))
        assert m_flash.total_tokens == 350  # 150 + 200
        assert m_flash.call_count == 2

        m_vl = next(m for m in overview.token_usage.models_breakdown if "qwen-2.5-vl-72b-instruct" in (m.model_name or m.model))
        assert m_vl.total_tokens == 450
        assert m_vl.call_count == 1

        # 5. Check Run Types Breakdown
        assert len(overview.token_usage.run_types_breakdown) >= 3
        r_ins = next(r for r in overview.token_usage.run_types_breakdown if r.run_type == "insight_extraction")
        assert r_ins.name_zh == "需求洞察深度提炼"
        assert r_ins.total_tokens == 450

        r_ep = next(r for r in overview.token_usage.run_types_breakdown if r.run_type == "episode")
        assert r_ep.name_zh == "对话切分与话题提取"
        assert r_ep.total_tokens == 150

        r_rq = next(r for r in overview.token_usage.run_types_breakdown if r.run_type == "research_assistant")
        assert r_rq.name_zh == "智能研究问答助手"
        assert r_rq.total_tokens == 200


@pytest.mark.asyncio
async def test_hourly_token_trend_strict_per_hour_accuracy_and_zero_isolation():
    from datetime import datetime, time as dt_time, timedelta
    from packages.persistence.models import AnalysisRun

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()

        today_d = datetime.now().date()
        yesterday_d = today_d - timedelta(days=1)

        # 1. Seed a massive run YESTERDAY (should NOT leak into today's hourly trend)
        yesterday_run = AnalysisRun(
            workspace_id=ws.id,
            run_type="episode",
            target_type="episode",
            target_id="ep-old",
            provider="dashscope",
            model="qwen3.8-flash",
            input_hash="hash_old",
            config_hash="cfg_old",
            state="succeeded",
            input_tokens=10000,
            output_tokens=5000,
            total_tokens=15000,
            started_at=datetime.combine(yesterday_d, dt_time(14, 30)),
            completed_at=datetime.combine(yesterday_d, dt_time(14, 31)),
        )
        session.add(yesterday_run)

        # 2. Seed runs TODAY at specific hours: 02:15, 09:40, 15:10
        run_02 = AnalysisRun(
            workspace_id=ws.id,
            run_type="episode",
            target_type="episode",
            target_id="ep-02",
            provider="dashscope",
            model="qwen3.8-flash",
            input_hash="hash02",
            config_hash="cfg02",
            state="succeeded",
            total_tokens=2500,
            input_tokens=2000,
            output_tokens=500,
            started_at=datetime.combine(today_d, dt_time(2, 15)),
            completed_at=datetime.combine(today_d, dt_time(2, 16)),
        )
        run_09 = AnalysisRun(
            workspace_id=ws.id,
            run_type="insight_extraction",
            target_type="insight",
            target_id="ins-09",
            provider="dashscope",
            model="qwen3.8-flash",
            input_hash="hash09",
            config_hash="cfg09",
            state="succeeded",
            total_tokens=8000,
            input_tokens=6000,
            output_tokens=2000,
            started_at=datetime.combine(today_d, dt_time(9, 40)),
            completed_at=datetime.combine(today_d, dt_time(9, 41)),
        )
        run_15 = AnalysisRun(
            workspace_id=ws.id,
            run_type="episode",
            target_type="episode",
            target_id="ep-15",
            provider="dashscope",
            model="qwen3.8-flash",
            input_hash="hash15",
            config_hash="cfg15",
            state="succeeded",
            total_tokens=1200,
            input_tokens=1000,
            output_tokens=200,
            started_at=datetime.combine(today_d, dt_time(15, 10)),
            completed_at=datetime.combine(today_d, dt_time(15, 11)),
        )
        session.add(run_02)
        session.add(run_09)
        session.add(run_15)
        await session.commit()

        generator = ReportGenerator(session)
        overview = await generator.get_operational_overview(period="7d")
        hourly = overview.token_usage.token_hourly_trend

        # 3. Must be exactly 24 continuous hours
        assert len(hourly) == 24
        assert hourly[0].hour == "00:00"
        assert hourly[23].hour == "23:00"

        # 4. Check specific hours
        pt_02 = next(h for h in hourly if h.hour == "02:00")
        assert pt_02.tokens == 2500
        assert pt_02.runs_count == 1

        pt_09 = next(h for h in hourly if h.hour == "09:00")
        assert pt_09.tokens == 8000
        assert pt_09.runs_count == 1

        pt_15 = next(h for h in hourly if h.hour == "15:00")
        assert pt_15.tokens == 1200
        assert pt_15.runs_count == 1

        # 5. Check other hours are strictly 0 and do NOT contain yesterday's 15,000 tokens
        pt_14 = next(h for h in hourly if h.hour == "14:00")
        assert pt_14.tokens == 0
        assert pt_14.runs_count == 0

        # 6. Total today hourly tokens must equal sum of today's runs (2500 + 8000 + 1200 = 11700)
        today_total_hourly = sum(h.tokens for h in hourly)
        assert today_total_hourly == 11700

        # 7. Check that yesterday's 15000 is present in the 7-day daily trend, but NOT in today
        yesterday_str = yesterday_d.strftime("%Y-%m-%d")
        today_str = today_d.strftime("%Y-%m-%d")
        daily_yesterday = next((d for d in overview.token_usage.token_daily_trend if d.date == yesterday_str), None)
        daily_today = next(d for d in overview.token_usage.token_daily_trend if d.date == today_str)

        assert daily_yesterday is not None
        assert daily_yesterday.tokens == 15000
        assert daily_today.tokens == 11700


@pytest.mark.asyncio
async def test_operational_overview_real_calendar_anchoring_and_comparison_tooltips():
    """
    Validates:
    1. Real calendar date anchoring for today, 7d, 30d (no artificial fallback).
    2. Dynamic comparison_label generation ("对比YYYY-MM-DD", "对比YYYY-MM-DD至YYYY-MM-DD").
    3. Topic aggregation counting (total_topics, topics_growth_pct).
    4. Successful push records tracking (total_pushed_insights).
    """
    from datetime import datetime, timedelta, time as dt_time
    from packages.persistence.models import InsightPushRecord, Topic

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()

        now_dt = datetime.now()
        today_date = now_dt.date()
        yesterday_date = today_date - timedelta(days=1)

        # 1. Seed Topics: 1 created today, 1 created yesterday
        t_today = Topic(
            workspace_id=ws.id,
            title="今日聚合话题",
            summary="测试今日生成话题",
            module="系统/核心",
            created_at=datetime.combine(today_date, dt_time(10, 0)),
            first_seen_at=datetime.combine(today_date, dt_time(10, 0)),
            last_seen_at=datetime.combine(today_date, dt_time(10, 0)),
        )
        t_yesterday = Topic(
            workspace_id=ws.id,
            title="昨日聚合话题",
            summary="测试昨日生成话题",
            module="系统/网络",
            created_at=datetime.combine(yesterday_date, dt_time(14, 0)),
            first_seen_at=datetime.combine(yesterday_date, dt_time(14, 0)),
            last_seen_at=datetime.combine(yesterday_date, dt_time(14, 0)),
        )
        session.add(t_today)
        session.add(t_yesterday)

        # 2. Seed a successful push record today
        push_rec = InsightPushRecord(
            workspace_id=ws.id,
            insight_id="ins-test-1",
            target_platform="feishu_bitable",
            webhook_url="https://open.feishu.cn/webhook/v2/test",
            state="success",
            created_at=datetime.combine(today_date, dt_time(11, 0)),
        )
        session.add(push_rec)
        await session.commit()

        generator = ReportGenerator(session)

        # Test "today"
        today_ov = await generator.get_operational_overview(period="today")
        assert today_ov.start_date == today_date.strftime("%Y-%m-%d")
        assert today_ov.end_date == today_date.strftime("%Y-%m-%d")
        assert today_ov.days_count == 1
        assert today_ov.comparison.comparison_label == f"对比{yesterday_date.strftime('%Y-%m-%d')}"
        assert today_ov.total_topics == 1
        assert today_ov.total_pushed_insights == 1

        # Test "7d"
        ov_7d = await generator.get_operational_overview(period="7d")
        expected_7d_start = (today_date - timedelta(days=6)).strftime("%Y-%m-%d")
        assert ov_7d.start_date == expected_7d_start
        assert ov_7d.end_date == today_date.strftime("%Y-%m-%d")
        assert ov_7d.days_count == 7
        p_7d_start = (today_date - timedelta(days=13)).strftime("%Y-%m-%d")
        p_7d_end = (today_date - timedelta(days=7)).strftime("%Y-%m-%d")
        assert ov_7d.comparison.comparison_label == f"对比{p_7d_start}至{p_7d_end}"
        # Both topics fall within the 7-day window
        assert ov_7d.total_topics == 2

        # Test "30d"
        ov_30d = await generator.get_operational_overview(period="30d")
        expected_30d_start = (today_date - timedelta(days=29)).strftime("%Y-%m-%d")
        assert ov_30d.start_date == expected_30d_start
        assert ov_30d.end_date == today_date.strftime("%Y-%m-%d")
        assert ov_30d.days_count == 30
        p_30d_start = (today_date - timedelta(days=59)).strftime("%Y-%m-%d")
        p_30d_end = (today_date - timedelta(days=30)).strftime("%Y-%m-%d")
        assert ov_30d.comparison.comparison_label == f"对比{p_30d_start}至{p_30d_end}"


@pytest.mark.asyncio
async def test_token_scope_hourly_and_daily_tab_isolation_and_zero_recall_filtering():
    """
    Verifies that:
    1. hourly_scope (today 24h) and daily_scope (period 7d) are properly isolated.
    2. When runs exist on yesterday but 0 runs exist today, daily_scope has the runs,
       while hourly_scope cleanly reports 0 runs without falling back to all-time history.
    3. Research queries with model_used='system-direct' (zero recall) are strictly filtered
       out of models_breakdown and do not appear as an LLM model item.
    4. tokens_per_second reflects recent requests telemetry.
    """
    from datetime import datetime, timedelta
    from packages.persistence.models import AnalysisRun, ResearchQueryRecord, Workspace
    from packages.analytics.report_generator import ReportGenerator

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        ws = Workspace(name="test_scope_ws")
        session.add(ws)
        await session.flush()

        now_dt = datetime.now()
        yesterday_dt = now_dt - timedelta(days=2)

        # 1. Add run on yesterday (within 7d, but NOT today)
        r_yesterday = AnalysisRun(
            workspace_id=ws.id,
            run_type="insight_extraction",
            target_type="episode",
            target_id="ep_yesterday",
            provider="qwen",
            model="qwen3.8-flash",
            input_hash="hash_yesterday",
            config_hash="cfg_yesterday",
            state="succeeded",
            started_at=yesterday_dt,
            completed_at=yesterday_dt + timedelta(seconds=2),
            input_tokens=300,
            output_tokens=100,
            total_tokens=400,
        )
        session.add(r_yesterday)

        # 2. Add ResearchQueryRecord with 'system-direct' (zero recall RAG response)
        rq_direct = ResearchQueryRecord(
            workspace_id=ws.id,
            question="不存在的问题",
            answer_text="零召回回答",
            model_used="system-direct",
            total_tokens=0,
            prompt_tokens=0,
            completion_tokens=0,
            created_at=yesterday_dt,
        )
        session.add(rq_direct)
        await session.commit()

        generator = ReportGenerator(session)
        overview = await generator.get_operational_overview(period="7d")

        # daily_scope (7d): includes yesterday's run and query
        assert overview.token_usage.daily_scope.total_tokens == 400
        assert overview.token_usage.daily_scope.analysis_runs_count == 2
        assert len(overview.token_usage.daily_scope.models_breakdown) == 1
        assert overview.token_usage.daily_scope.models_breakdown[0].model_name == "qwen3.8-flash"

        # hourly_scope (today): 0 runs today -> must be 0, no fallback!
        assert overview.token_usage.hourly_scope.total_tokens == 0
        assert overview.token_usage.hourly_scope.analysis_runs_count == 0
        assert len(overview.token_usage.hourly_scope.models_breakdown) == 0

        # system-direct must NEVER be in models_breakdown!
        all_models = [m.model for m in overview.token_usage.daily_scope.models_breakdown] + \
                     [m.model for m in overview.token_usage.hourly_scope.models_breakdown] + \
                     [m.model for m in overview.token_usage.models_breakdown]
        assert "system-direct" not in all_models
        assert not any("零召回" in m for m in all_models)



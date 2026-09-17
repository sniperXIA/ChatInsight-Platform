import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import app
from packages.persistence.db import Base, get_session


@pytest.mark.asyncio
async def test_pipeline_observability_endpoints(tmp_path):
    test_db_path = tmp_path / 'test_pipeline.db'
    test_engine = create_async_engine(f'sqlite+aiosqlite:///{test_db_path}', echo=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    sample_source = tmp_path / "sample_chat_root"
    sample_group = sample_source / "20260820" / "LiberLive 核心玩家群"
    sample_group.mkdir(parents=True, exist_ok=True)
    (sample_group / "聊天记录.txt").write_text(
        "2026-08-20 10:00:00 玩家小李:\n请问扩展音色卡为什么没声音？\n2026-08-20 10:02:00 客服小凡:\n请重新插拔一下音色卡。\n",
        encoding="utf-8",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://testserver') as client:
        # 1. Test single step scan & import (mock / sample)
        step1_res = await client.post('/api/v1/pipeline/step/scan_import', json={
            'source_path': str(sample_source),
            'force_mock': True,
            'limit_batches': 2,
        })
        assert step1_res.status_code == 200
        step1_data = step1_res.json()
        assert step1_data['step_id'] == 'scan_import'
        assert 'logs' in step1_data

        # 2. Test single step segmentation
        step3_res = await client.post('/api/v1/pipeline/step/segmentation', json={
            'force_mock': True,
            'limit_batches': 2,
        })
        assert step3_res.status_code == 200
        step3_data = step3_res.json()
        assert step3_data['step_id'] == 'segmentation'

        # 3. Test single step insights
        step4_res = await client.post('/api/v1/pipeline/step/insights', json={
            'force_mock': True,
            'limit_episodes': 5,
        })
        assert step4_res.status_code == 200
        step4_data = step4_res.json()
        assert step4_data['step_id'] == 'insight_extraction'

        # 4. Test full pipeline run with detailed logs
        pipe_res = await client.post('/api/v1/pipeline/run', json={
            'source_path': str(sample_source),
            'force_mock': True,
            'limit_batches': 2,
            'limit_episodes': 5,
            'clean_previous_insights': True,
        })
        assert pipe_res.status_code == 200
        pipe_data = pipe_res.json()
        assert pipe_data['success'] is True
        assert len(pipe_data['steps']) >= 6
        for st in pipe_data['steps']:
            assert 'step_id' in st
            assert 'status' in st
            assert 'logs' in st

    app.dependency_overrides.clear()
    await test_engine.dispose()

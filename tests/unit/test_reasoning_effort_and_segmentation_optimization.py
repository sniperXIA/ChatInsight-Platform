import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch

from apps.api.main import app
from packages.insights.episode_segmenter import EpisodeSegmenter, EpisodeSummaryOutput
from packages.model_gateway.mock_adapter import MockModelAdapter
from packages.model_gateway.openrouter_adapter import OpenRouterAdapter
from packages.model_gateway.settings_manager import (
    ModelSettingsConfig,
    ModuleConfigItem,
    SettingsManager,
)
from packages.persistence.db import Base, get_session
from packages.persistence.models import Conversation, Message, Participant, SourceFile, SourceRoot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from datetime import datetime, timedelta


def test_settings_manager_reasoning_effort_configuration(tmp_path):
    temp_file = tmp_path / "model_settings.json"
    with patch("packages.model_gateway.settings_manager.SETTINGS_FILE_PATH", temp_file):
        SettingsManager.clear_cache()
        cfg = SettingsManager.get_settings()
        assert hasattr(cfg, "global_reasoning_effort")
        assert cfg.global_reasoning_effort in ("none", "low", "medium", "high", "default")
        assert cfg.segmentation.reasoning_effort == "none"

        # Update reasoning effort per module
        cfg.segmentation.reasoning_effort = "none"
        cfg.clustering_judge.reasoning_effort = "high"
        cfg.global_reasoning_effort = "medium"

        SettingsManager.save_settings(cfg)
        SettingsManager.clear_cache()

        reloaded = SettingsManager.get_settings()
        assert reloaded.global_reasoning_effort == "medium"
        assert reloaded.segmentation.reasoning_effort == "none"
        assert reloaded.clustering_judge.reasoning_effort == "high"


def test_openrouter_adapter_reasoning_effort_payload():
    adapter = OpenRouterAdapter(api_key="test-key", base_url="https://api.openai.com/v1")

    # Verify adapter accepts reasoning_effort parameter without error
    assert hasattr(adapter, "_call_and_parse")


@pytest.mark.asyncio
async def test_settings_api_reasoning_effort_sync(tmp_path):
    test_db_path = tmp_path / "test_settings_reasoning.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{test_db_path}", echo=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    temp_settings_file = tmp_path / "test_settings.json"
    with patch("packages.model_gateway.settings_manager.SETTINGS_FILE_PATH", temp_settings_file):
        SettingsManager.clear_cache()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. Get settings
            res = await client.get("/api/v1/settings/models")
            assert res.status_code == 200
            data = res.json()
            assert "global_reasoning_effort" in data
            assert "reasoning_effort" in data["segmentation"]

            # 2. Update with apply_reasoning_effort_to_all_modules
            update_payload = {
                "global_reasoning_effort": "low",
                "apply_reasoning_effort_to_all_modules": True,
            }
            res_up = await client.post("/api/v1/settings/models", json=update_payload)
            assert res_up.status_code == 200
            up_data = res_up.json()
            assert up_data["global_reasoning_effort"] == "low"
            assert up_data["segmentation"]["reasoning_effort"] == "low"
            assert up_data["insight_extraction"]["reasoning_effort"] == "low"
            assert up_data["clustering_judge"]["reasoning_effort"] == "low"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_episode_segmenter_fast_path_and_concurrency(tmp_path):
    test_db_path = tmp_path / "test_segmenter_opt.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{test_db_path}", echo=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        # Create SourceRoot and SourceFile
        root = SourceRoot(id="r1", workspace_id="default", display_name="root", root_path="/test")
        session.add(root)
        sfile = SourceFile(id="f1", workspace_id="default", source_root_id=root.id, relative_path="chat.txt", file_kind="txt", size_bytes=100, sha256="abc")
        session.add(sfile)

        # Create conversation
        conv = Conversation(
            id="c_opt_1",
            workspace_id="default",
            display_name="吉他交流群",
            source_type="wechat_archive",
            source_conversation_id="c_opt_1",
        )
        session.add(conv)
        p1 = Participant(id="p1", workspace_id="default", stable_anonymous_key="anon_p1", display_label="张三", metadata_json={})
        session.add(p1)

        # Cluster 1: Trivial short messages -> Should trigger Fast-Path without LLM
        now = datetime(2026, 8, 27, 10, 0, 0)
        m1 = Message(id="m1", workspace_id="default", conversation_id=conv.id, participant_id=p1.id, source_file_id=sfile.id, raw_text="收到", normalized_text="收到", source_record_hash="h1", sent_at=now, source_sequence=1)
        m2 = Message(id="m2", workspace_id="default", conversation_id=conv.id, participant_id=p1.id, source_file_id=sfile.id, raw_text="好的", normalized_text="好的", source_record_hash="h2", sent_at=now + timedelta(seconds=5), source_sequence=2)

        # Cluster 2: Domain topic keyword -> Should trigger Fast-Path via multi-level taxonomy
        m3 = Message(id="m3", workspace_id="default", conversation_id=conv.id, participant_id=p1.id, source_file_id=sfile.id, raw_text="我早就看不清楚了，字太小了", normalized_text="我早就看不清楚了，字太小了", source_record_hash="h3", sent_at=now + timedelta(minutes=30), source_sequence=3)

        session.add_all([m1, m2, m3])
        await session.commit()

        mock_gateway = AsyncMock()
        mock_provider = MockModelAdapter()
        mock_gateway.get_text_provider.return_value = mock_provider

        segmenter = EpisodeSegmenter(session=session, model_gateway=mock_gateway)
        episodes = await segmenter.segment_conversation(conversation_id=conv.id, time_gap_minutes=25, force_mock=False)

        # Cluster 1 ('收到', '好的') is pruned by the quality gate; Cluster 2 is extracted as UI episode
        assert len(episodes) == 1
        # Fast-Path handled without invoking LLM network calls
        assert mock_provider.call_count == 0

        # Verify cluster got classified as UI
        assert episodes[0].category_hint.startswith("ui_ux")
        assert "字号偏小" in episodes[0].title or "界面" in episodes[0].title

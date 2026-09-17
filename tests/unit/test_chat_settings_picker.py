import sys
import subprocess
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import app
from apps.api.routes.settings import _open_native_folder_dialog
from packages.importers.chat_settings import ChatSettingsManager
from packages.persistence.db import Base, get_session


@pytest_asyncio.fixture
async def client(tmp_path):
    test_db_path = tmp_path / "test_picker.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{test_db_path}", echo=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.clear()
    await test_engine.dispose()


def test_list_drives_and_subdirs_roots():
    """Test that list_drives_and_subdirs with None/empty path returns root locations with entries."""
    res = ChatSettingsManager.list_drives_and_subdirs(None)
    assert res["current_path"] == ""
    assert res["parent_path"] is None
    assert len(res["directories"]) > 0
    for d in res["directories"]:
        assert "name" in d
        assert "path" in d
        assert "icon" in d


def test_list_drives_and_subdirs_invalid_path_fallback():
    """Test that non-existent paths (e.g. D:/ on Mac) gracefully fallback to roots with an error note."""
    res = ChatSettingsManager.list_drives_and_subdirs("/non_existent_folder_xyz_9999")
    assert res["current_path"] == ""
    assert res["parent_path"] is None
    assert len(res["directories"]) > 0
    assert "error" in res
    assert "不存在" in res["error"]


def test_list_drives_and_subdirs_valid_directory(tmp_path):
    """Test that an existing directory lists its subdirectories and ignores files."""
    sub1 = tmp_path / "subfolder_a"
    sub1.mkdir()
    sub2 = tmp_path / "subfolder_b"
    sub2.mkdir()
    txt_file = tmp_path / "ignored.txt"
    txt_file.write_text("hello")

    res = ChatSettingsManager.list_drives_and_subdirs(str(tmp_path))
    assert res["current_path"] == str(tmp_path.resolve()).replace("\\", "/")
    names = [d["name"] for d in res["directories"]]
    assert "subfolder_a" in names
    assert "subfolder_b" in names
    assert "ignored.txt" not in names


def test_open_native_folder_dialog_darwin_success():
    """Test macOS osascript folder picker on success."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "/Users/test/chat_data/\n"

    with patch("sys.platform", "darwin"), patch("subprocess.run", return_value=mock_proc):
        folder = _open_native_folder_dialog("/Users/test")
        assert folder == "/Users/test/chat_data"


def test_open_native_folder_dialog_darwin_cancel():
    """Test macOS osascript folder picker when user cancels (exit code 1 / error -128)."""
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = ""
    mock_proc.stderr = "execution error: User canceled (-128)"

    with patch("sys.platform", "darwin"), patch("subprocess.run", return_value=mock_proc):
        folder = _open_native_folder_dialog("/Users/test")
        assert folder == ""


def test_open_native_folder_dialog_windows_success():
    """Test Windows PowerShell folder picker on success."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "D:\\wechat\\records\r\n"

    with patch("sys.platform", "win32"), patch("subprocess.run", return_value=mock_proc):
        folder = _open_native_folder_dialog("D:/wechat")
        assert folder == "D:/wechat/records"


def test_open_native_folder_dialog_linux_zenity():
    """Test Linux zenity folder picker on success."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "/home/user/chat_data\n"

    with patch("sys.platform", "linux"), patch("subprocess.run", return_value=mock_proc):
        folder = _open_native_folder_dialog("/home/user")
        assert folder == "/home/user/chat_data"


@pytest.mark.asyncio
async def test_api_browse_folder_success(client, tmp_path):
    """Test /api/v1/settings/browse-folder when user selects a valid path."""
    selected_dir = tmp_path / "wechat_export"
    selected_dir.mkdir()
    fake_config = tmp_path / "test_browse_cfg.json"

    with patch("packages.importers.chat_settings.CHAT_SETTINGS_FILE", fake_config):
        with patch("apps.api.routes.settings._open_native_folder_dialog", return_value=str(selected_dir)):
            resp = await client.post("/api/v1/settings/browse-folder", json={"initial_dir": ""})
            assert resp.status_code == 200
            data = resp.json()
            assert data["cancelled"] is False
            assert data["selected_path"] == str(selected_dir).replace("\\", "/")
            assert "validation" in data


@pytest.mark.asyncio
async def test_api_browse_folder_cancelled(client):
    """Test /api/v1/settings/browse-folder when user cancels."""
    with patch("apps.api.routes.settings._open_native_folder_dialog", return_value=""):
        resp = await client.post("/api/v1/settings/browse-folder", json={"initial_dir": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert data["cancelled"] is True
        assert data["selected_path"] == ""


@pytest.mark.asyncio
async def test_api_browse_tree(client, tmp_path):
    """Test /api/v1/settings/browse-tree endpoint."""
    resp = await client.get("/api/v1/settings/browse-tree")
    assert resp.status_code == 200
    data = resp.json()
    assert data["current_path"] == ""
    assert len(data["directories"]) > 0

    sub = tmp_path / "test_folder"
    sub.mkdir()
    resp_sub = await client.get(f"/api/v1/settings/browse-tree?path={tmp_path}")
    assert resp_sub.status_code == 200
    data_sub = resp_sub.json()
    assert data_sub["current_path"] == str(tmp_path.resolve()).replace("\\", "/")
    assert any(d["name"] == "test_folder" for d in data_sub["directories"])


def test_get_default_source_path_resolves():
    """Test that get_default_source_path returns an existing or valid string path."""
    default_path = ChatSettingsManager.get_default_source_path()
    assert isinstance(default_path, str)
    assert len(default_path) > 0


def test_load_settings_self_healing(tmp_path):
    """Test that when saved settings contain an invalid Windows path on macOS/Linux, it self-heals to default path."""
    fake_config = tmp_path / "fake_chat_settings.json"
    import json
    fake_config.write_text(
        json.dumps({"source_path": "D:/definitely_non_existent_folder_9999", "watchdog_enabled": False}),
        encoding="utf-8"
    )

    with patch("packages.importers.chat_settings.CHAT_SETTINGS_FILE", fake_config):
        settings = ChatSettingsManager.load_settings()
        # On macOS/Linux, it should have healed to local default
        if sys.platform != "win32":
            assert settings.source_path != "D:/definitely_non_existent_folder_9999"
            assert "fake_chat_settings.json" in str(fake_config)


@pytest.mark.asyncio
async def test_api_watchdog_status(client):
    """Test GET /api/v1/settings/watchdog/status endpoint."""
    resp = await client.get("/api/v1/settings/watchdog/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "is_running" in data
    assert "event_count" in data


@pytest.mark.asyncio
async def test_api_browse_folder_auto_saves(client, tmp_path):
    """Test /api/v1/settings/browse-folder auto-saves the selected folder to settings."""
    selected_dir = tmp_path / "wechat_saved"
    selected_dir.mkdir()

    fake_config = tmp_path / "test_auto_save_settings.json"
    with patch("packages.importers.chat_settings.CHAT_SETTINGS_FILE", fake_config):
        with patch("apps.api.routes.settings._open_native_folder_dialog", return_value=str(selected_dir)):
            resp = await client.post("/api/v1/settings/browse-folder", json={"initial_dir": "", "auto_save": True})
            assert resp.status_code == 200
            data = resp.json()
            assert data["cancelled"] is False
            assert data["selected_path"] == str(selected_dir).replace("\\", "/")
            assert data.get("settings") is not None
            assert data["settings"]["source_path"] == str(selected_dir).replace("\\", "/")
            assert fake_config.exists()


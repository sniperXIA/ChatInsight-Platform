import os
import json
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, Field


CHAT_SETTINGS_FILE = Path("config/chat_settings.json")


def get_default_source_path() -> str:
    """Finds the best available default source path on the host OS."""
    cwd = Path.cwd()
    candidates = [
        cwd / "Demo用户聊天数据",
        cwd / "sample_data",
        Path.home() / "Demo用户聊天数据",
    ]
    for cand in candidates:
        if cand.exists() and cand.is_dir():
            return str(cand.resolve()).replace("\\", "/")
    import sys
    if sys.platform == "win32":
        return "D:/玩家群聊天信息2"
    return str(cwd).replace("\\", "/")


DEFAULT_SOURCE_PATH = get_default_source_path()


class ChatSettings(BaseModel):
    source_path: str = Field(default_factory=get_default_source_path, description="微信聊天记录最外层根目录")
    watchdog_enabled: bool = Field(default=False, description="是否启用操作系统文件变更实时监听")
    debounce_seconds: float = Field(default=5.0, description="文件写入防抖静默时间(秒)")
    auto_import_on_change: bool = Field(default=True, description="检测到文件变更后自动增量导入入库")
    last_updated: Optional[str] = None


class PathValidationReport(BaseModel):
    is_valid: bool
    exists: bool
    is_dir: bool
    path: str
    absolute_path: str = ""
    is_too_deep: bool = False
    warning_message: Optional[str] = None
    date_folders_count: int = 0
    date_folders: list[str] = Field(default_factory=list)
    group_folders_count: int = 0
    group_folders: list[str] = Field(default_factory=list)
    txt_files_count: int = 0
    image_files_count: int = 0
    total_size_mb: float = 0.0


class ChatSettingsManager:
    """Manages chat archive path configuration and folder structure validation."""

    @classmethod
    def get_default_source_path(cls) -> str:
        return get_default_source_path()

    @classmethod
    def load_settings(cls) -> ChatSettings:
        settings: Optional[ChatSettings] = None
        if CHAT_SETTINGS_FILE.exists():
            try:
                with open(CHAT_SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    settings = ChatSettings(**data)
            except Exception:
                pass

        default_path = cls.get_default_source_path()
        if not settings:
            settings = ChatSettings(source_path=default_path)
            cls.save_settings(settings)
            return settings

        # Self-healing: if configured source_path does not exist on this machine
        # (e.g. Windows path D:/... on macOS), and a valid local candidate exists,
        # seamlessly heal it to the local candidate and persist it!
        raw_p = (settings.source_path or "").strip().replace("\\", "/")
        if not raw_p or not Path(raw_p).exists():
            if default_path and Path(default_path).exists():
                settings.source_path = default_path
                cls.save_settings(settings)

        return settings

    @classmethod
    def save_settings(cls, settings: ChatSettings) -> ChatSettings:
        CHAT_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        import datetime
        settings.last_updated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(CHAT_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings.model_dump(), f, ensure_ascii=False, indent=2)
        return settings

    @classmethod
    def validate_path(cls, path_str: str) -> PathValidationReport:
        clean_path = (path_str or "").strip().replace("\\", "/")
        if not clean_path:
            return PathValidationReport(
                is_valid=False,
                exists=False,
                is_dir=False,
                path=path_str,
                warning_message="路径不能为空",
            )

        p = Path(clean_path)
        if not p.exists():
            return PathValidationReport(
                is_valid=False,
                exists=False,
                is_dir=False,
                path=clean_path,
                warning_message=f"指定的路径在当前系统中不存在: {clean_path}",
            )

        if not p.is_dir():
            return PathValidationReport(
                is_valid=False,
                exists=True,
                is_dir=False,
                path=clean_path,
                warning_message="指定的路径是一个文件而非文件夹，请选择文件夹根目录",
            )

        abs_path = str(p.resolve()).replace("\\", "/")

        # Inspect child directories
        date_folders = []
        group_folders = set()
        txt_files_count = 0
        img_files_count = 0
        total_bytes = 0

        # Check if root directly contains txt files (anti-pattern: selected a group folder directly)
        direct_txts = []
        try:
            for f in p.iterdir():
                try:
                    if f.is_file() and f.suffix.lower() == ".txt":
                        direct_txts.append(f)
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError):
            pass

        if direct_txts:
            # User selected a single group folder!
            return PathValidationReport(
                is_valid=True,
                exists=True,
                is_dir=True,
                path=clean_path,
                absolute_path=abs_path,
                is_too_deep=True,
                warning_message=(
                    "⚠️ 检测到您选择的文件夹下直接包含了聊天记录 .txt 文件！"
                    "您疑似直接选到了【具体群聊文件夹】层级。"
                    "强烈建议您选择该文件夹的上一级（总根目录），以便系统能够自动发现并处理多个群聊和不同日期！"
                ),
                txt_files_count=len(direct_txts),
                group_folders_count=1,
                group_folders=[p.name],
            )

        # Check if folder is named "images"
        if p.name.lower() == "images":
            return PathValidationReport(
                is_valid=False,
                exists=True,
                is_dir=True,
                path=clean_path,
                absolute_path=abs_path,
                is_too_deep=True,
                warning_message="⚠️ 您选择的是【images】图片素材文件夹，请选择包含所有日期和群聊的最外层总根目录！",
            )

        # Check if root folder itself is a single date folder (e.g. 20260216 or 20260216聊天记录)
        import re
        if re.match(r"^\d{8}(?:聊天记录)?$", p.name):
            sub_dirs = []
            try:
                for d in p.iterdir():
                    try:
                        if d.is_dir():
                            sub_dirs.append(d.name)
                    except (PermissionError, OSError):
                        continue
            except (PermissionError, OSError):
                pass
            return PathValidationReport(
                is_valid=True,
                exists=True,
                is_dir=True,
                path=clean_path,
                absolute_path=abs_path,
                is_too_deep=True,
                warning_message=(
                    f"⚠️ 您当前选择的是单一日期归档文件夹【{p.name}】！"
                    "建议您选择该文件夹的上一层目录（包含所有日期的总文件夹），以便平台能够持续自动扫描跨多日的全部聊天记录！"
                ),
                date_folders_count=1,
                date_folders=[p.name],
                group_folders_count=len(sub_dirs),
                group_folders=sub_dirs[:10],
            )

        try:
            items_checked = 0
            for item in p.iterdir():
                items_checked += 1
                if items_checked > 100:
                    break
                try:
                    if not item.is_dir() or item.name.startswith(".") or item.name.startswith("$"):
                        continue
                    
                    is_date = bool(re.match(r"^\d{4}[-_]?\d{2}[-_]?\d{2}(?:聊天记录)?$", item.name))
                    if is_date:
                        date_folders.append(item.name)
                        # Check subfolders in date directory
                        for sub in item.iterdir():
                            try:
                                if not sub.is_dir() or sub.name.startswith("."):
                                    continue
                                group_folders.add(sub.name)
                                for sub_file in sub.iterdir():
                                    try:
                                        if sub_file.is_file():
                                            total_bytes += sub_file.stat().st_size
                                            ext = sub_file.suffix.lower()
                                            if ext == ".txt":
                                                txt_files_count += 1
                                            elif ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                                                img_files_count += 1
                                        elif sub_file.is_dir() and sub_file.name.lower() == "images":
                                            for img in sub_file.iterdir():
                                                try:
                                                    if img.is_file() and img.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                                                        img_files_count += 1
                                                        total_bytes += img.stat().st_size
                                                except (PermissionError, OSError):
                                                    continue
                                    except (PermissionError, OSError):
                                        continue
                            except (PermissionError, OSError):
                                continue
                    else:
                        # Root directly contains group folders instead of date folders
                        has_txt = False
                        for sub_file in item.iterdir():
                            try:
                                if sub_file.is_file():
                                    ext = sub_file.suffix.lower()
                                    if ext == ".txt":
                                        has_txt = True
                                        txt_files_count += 1
                                        total_bytes += sub_file.stat().st_size
                                    elif ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                                        img_files_count += 1
                                        total_bytes += sub_file.stat().st_size
                            except (PermissionError, OSError):
                                continue
                        if has_txt:
                            group_folders.add(item.name)
                except (PermissionError, OSError):
                    continue
        except Exception as e:
            return PathValidationReport(
                is_valid=False,
                exists=True,
                is_dir=True,
                path=clean_path,
                absolute_path=abs_path,
                warning_message=f"读取文件夹内容时出错: {str(e)}",
            )

        is_valid = (len(date_folders) > 0 or txt_files_count > 0)
        warning = None
        if not is_valid:
            warning = "⚠️ 该目录下未检测到有效的日期归档文件夹或聊天记录，请检查是否选错了路径。"

        return PathValidationReport(
            is_valid=is_valid,
            exists=True,
            is_dir=True,
            path=clean_path,
            absolute_path=abs_path,
            is_too_deep=False,
            warning_message=warning,
            date_folders_count=len(date_folders),
            date_folders=sorted(date_folders)[:10],
            group_folders_count=len(group_folders),
            group_folders=sorted(list(group_folders))[:10],
            txt_files_count=txt_files_count,
            image_files_count=img_files_count,
            total_size_mb=round(total_bytes / (1024 * 1024), 2),
        )

    @classmethod
    def _get_root_locations(cls) -> list[dict[str, Any]]:
        """Retrieves cross-platform root locations (Windows drives, macOS directories/volumes, Linux roots/mounts)."""
        locations: list[dict[str, Any]] = []
        home = Path.home()
        if home.exists():
            locations.append({
                "name": f"用户主目录 ({home.name})",
                "path": str(home).replace("\\", "/"),
                "is_drive": True,
                "icon": "fa-house",
            })

        cwd = Path.cwd()
        locations.append({
            "name": f"项目工作区 ({cwd.name})",
            "path": str(cwd).replace("\\", "/"),
            "is_drive": True,
            "icon": "fa-briefcase",
        })

        import sys
        if sys.platform == "win32":
            import string
            for letter in string.ascii_uppercase:
                drive_str = f"{letter}:/"
                if os.path.exists(drive_str):
                    locations.append({
                        "name": f"本地磁盘 ({letter}:)",
                        "path": drive_str,
                        "is_drive": True,
                        "icon": "fa-hard-drive",
                    })
        else:
            root = Path("/")
            if root.exists():
                locations.append({
                    "name": "系统根目录 (/)",
                    "path": "/",
                    "is_drive": True,
                    "icon": "fa-hard-drive",
                })

            for label, sub in [
                ("桌面 (Desktop)", "Desktop"),
                ("文稿 (Documents)", "Documents"),
                ("下载 (Downloads)", "Downloads"),
            ]:
                p = home / sub
                if p.exists() and p.is_dir():
                    locations.append({
                        "name": label,
                        "path": str(p).replace("\\", "/"),
                        "is_drive": False,
                        "icon": "fa-folder",
                    })

            if sys.platform == "darwin":
                vols = Path("/Volumes")
                if vols.exists() and vols.is_dir():
                    try:
                        for v in sorted(vols.iterdir()):
                            if v.is_dir() and not v.name.startswith("."):
                                locations.append({
                                    "name": f"磁盘卷: {v.name}",
                                    "path": str(v).replace("\\", "/"),
                                    "is_drive": True,
                                    "icon": "fa-hdd",
                                })
                    except Exception:
                        pass
            else:
                for mount in ["/media", "/mnt"]:
                    mp = Path(mount)
                    if mp.exists() and mp.is_dir():
                        try:
                            for item in sorted(mp.iterdir()):
                                if item.is_dir() and not item.name.startswith("."):
                                    locations.append({
                                        "name": f"挂载卷: {item.name}",
                                        "path": str(item).replace("\\", "/"),
                                        "is_drive": True,
                                        "icon": "fa-hdd",
                                    })
                        except Exception:
                            pass

        return locations

    @classmethod
    def list_drives_and_subdirs(cls, current_path: Optional[str] = None) -> dict[str, Any]:
        """Lists available drives/roots or subdirectories under current_path across all operating systems."""
        if not current_path or current_path.strip() == "":
            return {
                "current_path": "",
                "parent_path": None,
                "directories": cls._get_root_locations(),
            }

        p = Path(current_path.strip().replace("\\", "/"))
        if not p.exists() or not p.is_dir():
            return {
                "current_path": "",
                "parent_path": None,
                "directories": cls._get_root_locations(),
                "error": f"指定路径 '{current_path}' 在当前系统中不存在，已为您切换展示可用系统位置。",
            }

        norm_path = str(p.resolve()).replace("\\", "/")
        parent_path = str(p.parent.resolve()).replace("\\", "/") if p.parent != p else None

        subdirs: list[dict[str, Any]] = []
        try:
            for item in p.iterdir():
                try:
                    if item.name.startswith(".") or item.name.startswith("$"):
                        continue
                    if item.is_dir():
                        subdirs.append({
                            "name": item.name,
                            "path": str(item.resolve()).replace("\\", "/"),
                            "is_drive": False,
                        })
                except (PermissionError, OSError):
                    continue
            subdirs.sort(key=lambda x: x["name"].lower())
        except PermissionError:
            return {
                "current_path": norm_path,
                "parent_path": parent_path,
                "directories": [],
                "error": "无权限访问此目录",
            }
        except Exception as e:
            return {
                "current_path": norm_path,
                "parent_path": parent_path,
                "directories": [],
                "error": str(e),
            }

        return {
            "current_path": norm_path,
            "parent_path": parent_path,
            "directories": subdirs,
        }

import asyncio
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

try:
    from watchdog.events import FileSystemEventHandler, FileSystemEvent
    from watchdog.observers import Observer
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    Observer = None
    FileSystemEventHandler = object

from packages.importers.chat_settings import ChatSettingsManager

logger = logging.getLogger(__name__)


class ChatArchiveEventHandler(FileSystemEventHandler):
    """Event handler that collects relevant file system events and triggers debounced sync."""

    RELEVANT_EXTS = {".txt", ".png", ".jpg", ".jpeg", ".webp", ".gif"}

    def __init__(self, watcher: "ChatDirectoryWatcher"):
        super().__init__()
        self.watcher = watcher

    def _is_relevant(self, path_str: str) -> bool:
        p = Path(path_str)
        if p.name.startswith(".") or p.name.startswith("~$") or p.name.endswith(".tmp"):
            return False
        return p.suffix.lower() in self.RELEVANT_EXTS

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory or self._is_relevant(event.src_path):
            self.watcher.record_event("created", event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_relevant(event.src_path):
            self.watcher.record_event("modified", event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        dest = getattr(event, "dest_path", event.src_path)
        if self._is_relevant(dest):
            self.watcher.record_event("moved", dest)


class ChatDirectoryWatcher:
    """
    Singleton watcher using native OS kernel file system event notifications (Watchdog/ReadDirectoryChangesW).
    Provides debounce buffering so that bursts of WeChat crawl exports settle before triggering incremental ingestion.
    """

    _instance: Optional["ChatDirectoryWatcher"] = None

    def __init__(self):
        self.observer: Optional[Observer] = None
        self.is_running: bool = False
        self.watched_path: Optional[str] = None
        self.debounce_seconds: float = 5.0
        self.auto_import: bool = True

        self.last_event_time: Optional[float] = None
        self.last_sync_time: Optional[str] = None
        self.last_sync_stats: Optional[dict[str, Any]] = None
        self.recent_events: list[dict[str, Any]] = []
        self.event_count: int = 0
        self.is_syncing: bool = False

        self._pending_sync: bool = False
        self._debounce_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    @classmethod
    def get_instance(cls) -> "ChatDirectoryWatcher":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def record_event(self, event_type: str, file_path: str) -> None:
        now = time.time()
        self.last_event_time = now
        self._pending_sync = True
        self.event_count += 1

        rel_name = os.path.basename(file_path)
        entry = {
            "type": event_type,
            "filename": rel_name,
            "path": file_path.replace("\\", "/"),
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }
        self.recent_events.insert(0, entry)
        if len(self.recent_events) > 15:
            self.recent_events.pop()

        logger.info(f"[Watchdog Event] {event_type.upper()}: {rel_name} recorded, debounce timer reset to {self.debounce_seconds}s")

    def start(self, source_path: str, debounce_seconds: float = 5.0, auto_import: bool = True) -> bool:
        if not WATCHDOG_AVAILABLE:
            logger.error("Watchdog is not available in current Python environment")
            return False

        clean_path = source_path.strip().replace("\\", "/")
        if not os.path.exists(clean_path) or not os.path.isdir(clean_path):
            logger.warning(f"Cannot start watcher: path {clean_path} does not exist or is not a directory")
            return False

        if self.is_running and self.watched_path == clean_path:
            self.debounce_seconds = debounce_seconds
            self.auto_import = auto_import
            return True

        self.stop()

        self.watched_path = clean_path
        self.debounce_seconds = debounce_seconds
        self.auto_import = auto_import
        self._stop_event.clear()

        try:
            handler = ChatArchiveEventHandler(self)
            self.observer = Observer()
            self.observer.schedule(handler, path=clean_path, recursive=True)
            self.observer.daemon = True
            self.observer.start()
            self.is_running = True

            # Start background debounce monitor thread
            self._debounce_thread = threading.Thread(target=self._debounce_loop, daemon=True)
            self._debounce_thread.start()

            logger.info(f"ChatDirectoryWatcher started on: {clean_path} (debounce={debounce_seconds}s, auto_import={auto_import})")
            return True
        except Exception as e:
            logger.error(f"Failed to start ChatDirectoryWatcher on {clean_path}: {e}", exc_info=True)
            self.stop()
            return False

    def stop(self) -> None:
        self._stop_event.set()
        if self.observer:
            try:
                self.observer.stop()
                self.observer.join(timeout=2.0)
            except Exception:
                pass
            self.observer = None

        self.is_running = False
        self._pending_sync = False
        logger.info("ChatDirectoryWatcher stopped")

    def _debounce_loop(self) -> None:
        """Background thread monitoring debounce quiet period."""
        while not self._stop_event.is_set():
            time.sleep(0.8)
            if self._pending_sync and self.last_event_time:
                elapsed = time.time() - self.last_event_time
                if elapsed >= self.debounce_seconds:
                    # Debounce period satisfied!
                    self._pending_sync = False
                    if self.auto_import and not self.is_syncing:
                        self._trigger_sync()

    def _trigger_sync(self) -> None:
        """Triggers incremental scan and import in a dedicated thread."""
        threading.Thread(target=self._run_incremental_import_sync, daemon=True).start()

    def _run_incremental_import_sync(self) -> None:
        if self.is_syncing or not self.watched_path:
            return

        self.is_syncing = True
        t0 = time.time()
        logger.info(f"[Watchdog Ingestion] Debounce period passed. Executing incremental import on {self.watched_path}...")

        try:
            # Run async scan and import
            from packages.importers.scanner import DirectoryScanner
            from packages.importers.batch_importer import BatchImporter
            from packages.persistence.db import get_session_context

            scanner = DirectoryScanner()
            report, batches = scanner.scan_root(root_path=self.watched_path)

            total_msg = 0
            total_media = 0
            already_completed_batches = 0

            async def _do_import():
                nonlocal total_msg, total_media, already_completed_batches
                async with get_session_context() as session:
                    importer = BatchImporter(session)
                    for b in batches:
                        stats = await importer.import_parsed_batch(b, root_path=self.watched_path)
                        total_msg += stats.messages_inserted
                        total_media += stats.media_inserted
                        if stats.already_completed:
                            already_completed_batches += 1

            asyncio.run(_do_import())

            duration = round(time.time() - t0, 2)
            self.last_sync_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.last_sync_stats = {
                "success": True,
                "duration_seconds": duration,
                "batches_scanned": len(batches),
                "messages_inserted": total_msg,
                "media_inserted": total_media,
                "already_completed_batches": already_completed_batches,
                "summary": f"增量入库完成：扫描 {len(batches)} 个批次，新增入库 {total_msg} 条消息、{total_media} 个媒体素材 (耗时 {duration}s)",
            }
            logger.info(f"[Watchdog Ingestion Complete] {self.last_sync_stats['summary']}")

        except Exception as e:
            logger.error(f"[Watchdog Ingestion Error] Failed to execute incremental import: {e}", exc_info=True)
            self.last_sync_stats = {
                "success": False,
                "error": str(e),
                "summary": f"增量入库失败: {str(e)}",
            }
        finally:
            self.is_syncing = False

    def get_status(self) -> dict[str, Any]:
        return {
            "watchdog_available": WATCHDOG_AVAILABLE,
            "is_running": self.is_running,
            "watched_path": self.watched_path,
            "debounce_seconds": self.debounce_seconds,
            "auto_import": self.auto_import,
            "is_syncing": self.is_syncing,
            "event_count": self.event_count,
            "last_event_time": datetime.fromtimestamp(self.last_event_time).strftime("%H:%M:%S") if self.last_event_time else None,
            "last_sync_time": self.last_sync_time,
            "last_sync_stats": self.last_sync_stats,
            "recent_events": self.recent_events[:8],
        }

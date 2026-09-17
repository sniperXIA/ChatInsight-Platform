import os
import re
from pathlib import Path
from typing import List, Optional

from packages.domain.models import generate_id, sha256_file, sha256_text
from packages.importers.contracts import ParsedBatch, PrecheckReport
from packages.importers.legacy_parser import LegacyTxtParser
from packages.importers.media_resolver import MediaResolver
from packages.importers.quality_reporter import QualityReporter

DATE_DIR_RE = re.compile(r"^(\d{8})(?:聊天记录)?$")


class DirectoryScanner:
    """Scans raw WeChat archive directories, parses chat text and media, and produces batches."""

    def __init__(self, timezone_str: str = "Asia/Shanghai"):
        self.timezone_str = timezone_str
        self.parser = LegacyTxtParser(timezone_str=timezone_str)
        self.media_resolver = MediaResolver()
        self.quality_reporter = QualityReporter()

    def scan_root(
        self,
        root_path: str,
        target_date: Optional[str] = None,
        target_group: Optional[str] = None,
    ) -> tuple[PrecheckReport, list[ParsedBatch]]:
        """Scan a root directory or specific date/group subdirectory and generate parsed batches & report."""
        root = Path(root_path)
        if not root.exists() or not root.is_dir():
            raise FileNotFoundError(f"Source root path does not exist: {root_path}")

        batches: list[ParsedBatch] = []
        scan_id = f"scan_{generate_id()[:18]}"

        # Check if root is directly a group folder (contains txt and images)
        if self._is_group_folder(root):
            date_str = datetime_to_date_str(root.stat().st_mtime)
            batch = self._process_group_folder(root, date_str=date_str, group_name=root.name)
            if batch:
                batches.append(batch)
        else:
            # Check if root is a date folder (contains group subfolders)
            date_match = DATE_DIR_RE.match(root.name)
            if date_match:
                date_str = date_match.group(1)
                for gdir in sorted(root.iterdir()):
                    if gdir.is_dir() and (not target_group or target_group in gdir.name):
                        batch = self._process_group_folder(gdir, date_str=date_str, group_name=gdir.name)
                        if batch:
                            batches.append(batch)
            else:
                # Root contains multiple date folders
                date_dirs = sorted([d for d in root.iterdir() if d.is_dir()])
                for ddir in date_dirs:
                    d_match = DATE_DIR_RE.match(ddir.name)
                    date_str = d_match.group(1) if d_match else ddir.name[:8]
                    if target_date and date_str != target_date:
                        continue

                    for gdir in sorted(ddir.iterdir()):
                        if gdir.is_dir() and (not target_group or target_group in gdir.name):
                            batch = self._process_group_folder(gdir, date_str=date_str, group_name=gdir.name)
                            if batch:
                                batches.append(batch)

        report = self.quality_reporter.build_precheck_report(
            scan_id=scan_id,
            root_path=str(root.resolve()),
            batches=batches,
        )
        return report, batches

    def _is_group_folder(self, folder: Path) -> bool:
        """Check if folder contains at least one txt file."""
        return any(f.is_file() and f.suffix.lower() == ".txt" for f in folder.iterdir())

    def _process_group_folder(self, group_dir: Path, date_str: str, group_name: str) -> Optional[ParsedBatch]:
        """Process a single group folder: parse TXT and discover media."""
        txt_files = [f for f in group_dir.iterdir() if f.is_file() and f.suffix.lower() == ".txt"]
        if not txt_files:
            return None

        # Pick the primary chat log TXT file
        txt_file = txt_files[0]
        for f in txt_files:
            if "聊天记录" in f.name:
                txt_file = f
                break

        # Calculate file hash
        file_sha256 = sha256_file(str(txt_file))
        file_content = self._read_text_safely(txt_file)
        lines = file_content.splitlines()

        # Parse messages
        drafts, unattached = self.parser.parse_lines(lines)
        conv_external_id = f"wxgroup_{sha256_text(group_name)[:16]}"
        messages = self.parser.convert_drafts_to_messages(
            drafts=drafts,
            source_file_sha256=file_sha256,
            conversation_external_id=conv_external_id,
        )

        # Discover and resolve media
        media_items = self.media_resolver.scan_group_media(group_dir)
        media_links, resolved_media = self.media_resolver.resolve_links(
            messages=messages,
            media_items=media_items,
            group_dir=group_dir,
        )

        batch_key = f"batch_{date_str}_{conv_external_id}"
        input_hash = sha256_text(f"{file_sha256}:{len(messages)}:{len(resolved_media)}:{len(media_links)}")

        return ParsedBatch(
            batch_key=batch_key,
            date_str=date_str,
            conversation_name=group_name,
            source_file_path=str(txt_file.resolve()),
            source_file_sha256=file_sha256,
            messages=messages,
            media_items=resolved_media,
            media_links=media_links,
            unattached_lines=unattached,
            input_hash=input_hash,
        )

    def _read_text_safely(self, file_path: Path) -> str:
        """Try common encodings for Chinese WeChat exports."""
        for enc in ["utf-8", "utf-8-sig", "gb18030", "gbk", "utf-16", "big5"]:
            try:
                return file_path.read_text(encoding=enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return file_path.read_text(encoding="utf-8", errors="replace")


def datetime_to_date_str(timestamp_epoch: float) -> str:
    from datetime import datetime

    dt = datetime.fromtimestamp(timestamp_epoch)
    return dt.strftime("%Y%m%d")

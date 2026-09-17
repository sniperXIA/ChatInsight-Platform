from datetime import datetime
from packages.importers.contracts import (
    DiagnosticIssue,
    ParsedBatch,
    PrecheckReport,
    PrecheckSummary,
)
from packages.domain.enums import MediaLinkState


class QualityReporter:
    """Calculates data quality metrics and produces precheck reports."""

    def build_precheck_report(self, scan_id: str, root_path: str, batches: list[ParsedBatch]) -> PrecheckReport:
        summary = PrecheckSummary()
        blocking: list[DiagnosticIssue] = []
        warnings: list[DiagnosticIssue] = []
        batch_summaries: list[dict] = []

        summary.conversation_count = len(batches)

        for batch in batches:
            msg_count = len(batch.messages)
            media_count = len(batch.media_items)
            summary.message_drafts += msg_count
            summary.media_files += media_count

            # Check unattached lines
            if batch.unattached_lines:
                for unattached in batch.unattached_lines:
                    warnings.append(
                        DiagnosticIssue(
                            code="UNATTACHED_LINE",
                            source_file=batch.source_file_path,
                            line=unattached.line_number,
                            message=f"未识别消息行: {unattached.raw_text[:60]}... ({unattached.reason})",
                        )
                    )

            # Check empty file
            if msg_count == 0:
                blocking.append(
                    DiagnosticIssue(
                        code="EMPTY_CHAT_FILE",
                        source_file=batch.source_file_path,
                        line=None,
                        message=f"群聊文件未解析出有效消息: {batch.source_file_path}",
                    )
                )

            # Media counts & states
            explicit_count = 0
            ambiguous_count = 0
            for link in batch.media_links:
                if link.state == MediaLinkState.CONFIRMED:
                    explicit_count += 1
                elif link.state == MediaLinkState.NEEDS_REVIEW:
                    ambiguous_count += 1
                    warnings.append(
                        DiagnosticIssue(
                            code="MEDIA_PLACEHOLDER_AMBIGUOUS",
                            source_file=batch.source_file_path,
                            line=None,
                            message=f"消息 {link.message_id} 与媒体 {link.media_filename} 的对应关系为候选推荐，待人工确认",
                        )
                    )

            summary.explicit_media_links += explicit_count
            summary.unresolved_image_placeholders += ambiguous_count

            batch_summaries.append(
                {
                    "batch_key": batch.batch_key,
                    "date_str": batch.date_str,
                    "conversation_name": batch.conversation_name,
                    "source_file_path": batch.source_file_path,
                    "message_count": msg_count,
                    "media_count": media_count,
                    "explicit_media_links": explicit_count,
                    "ambiguous_media_links": ambiguous_count,
                    "unattached_lines_count": len(batch.unattached_lines),
                    "input_hash": batch.input_hash,
                }
            )

        return PrecheckReport(
            scan_id=scan_id,
            source_root_path=root_path,
            summary=summary,
            blocking_issues=blocking,
            warnings=warnings,
            generated_at=datetime.now(),
            batches=batch_summaries,
        )

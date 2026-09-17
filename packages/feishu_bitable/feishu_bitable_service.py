from datetime import date, datetime, timedelta
import json
import logging
from pathlib import Path
from typing import Any, Optional
from sqlalchemy import and_, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.feishu_bitable.bitable_client import FeishuBitableClient
from packages.feishu_bitable.contracts import (
    BatchPushResponse,
    BitablePushResponse,
    BitablePushStats,
    FeishuBitableConfig,
    PushedInsightItem,
    PushHistoryRecordItem,
)
from packages.insights.tag_manager import TagManager
from packages.persistence.models import Insight, InsightPushRecord

logger = logging.getLogger(__name__)


class FeishuBitableService:
    """Service orchestrating Insight synchronization to Feishu Bitable."""

    def __init__(
        self,
        session: AsyncSession,
        config_path: str = "config/feishu_bitable_settings.json",
        client: FeishuBitableClient | None = None,
    ):
        self.session = session
        self.config_path = Path(config_path)
        self.client = client or FeishuBitableClient()

    def load_config(self) -> FeishuBitableConfig:
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return FeishuBitableConfig(**data)
            except Exception as e:
                logger.warning(f"Failed to read Feishu Bitable config: {e}")
        return FeishuBitableConfig()

    def save_config(self, config: FeishuBitableConfig) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config.model_dump(), f, ensure_ascii=False, indent=2)

    async def test_webhook(
        self,
        webhook_url: str,
        bearer_token: Optional[str] = None,
    ) -> tuple[bool, int, str]:
        success, code, msg = await self.client.test_connectivity(webhook_url, bearer_token=bearer_token)
        cfg = self.load_config()
        if cfg.webhook_url == webhook_url or not cfg.webhook_url:
            cfg.webhook_url = webhook_url
            if bearer_token is not None:
                cfg.bearer_token = bearer_token
                cfg.enable_token_auth = bool(bearer_token.strip())
            cfg.last_tested_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cfg.last_test_status = "success" if success else "failed"
            self.save_config(cfg)
        return success, code, msg

    async def push_single_insight(
        self,
        insight_id: str,
        operator: str = "user",
        force_mock: bool = False,
    ) -> BitablePushResponse:
        """Pushes a single insight to Feishu Bitable and records in history."""
        ins = await self.session.get(Insight, insight_id)
        if not ins:
            return BitablePushResponse(
                success=False,
                insight_id=insight_id,
                status_code=404,
                error=f"未找到 ID 为 {insight_id} 的需求洞察",
            )

        cfg = self.load_config()
        webhook_url = cfg.webhook_url.strip() if cfg.webhook_url else ""

        if not webhook_url and not force_mock:
            return BitablePushResponse(
                success=False,
                insight_id=insight_id,
                status_code=400,
                error="飞书多维表格 Webhook 地址未配置，请先在右上角【飞书多维表格 Webhook 设置】中配置有效地址",
            )

        bearer_token = cfg.bearer_token.strip() if (cfg.enable_token_auth and cfg.bearer_token) else (cfg.bearer_token.strip() if cfg.bearer_token else None)

        payload = self.client.format_insight_payload(ins)
        success, status_code, resp_body, err = await self.client.push_to_webhook(
            webhook_url=webhook_url,
            payload=payload,
            bearer_token=bearer_token,
            force_mock=force_mock,
        )

        # Record in InsightPushRecord table
        record = InsightPushRecord(
            workspace_id=ins.workspace_id,
            insight_id=ins.id,
            target_platform="feishu_bitable",
            webhook_url=webhook_url or "mock_webhook",
            payload_json=payload,
            state="success" if success else "failed",
            status_code=status_code,
            response_body=resp_body[:1000] if resp_body else "",
            error_message=err,
            operator=operator,
        )
        self.session.add(record)
        await self.session.commit()

        return BitablePushResponse(
            success=success,
            insight_id=ins.id,
            record_id=record.id,
            status_code=status_code,
            message="已成功推送到飞书多维表格！" if success else "推送失败",
            error=err,
        )

    async def push_batch_insights(
        self,
        insight_ids: list[str] | None = None,
        operator: str = "user",
        force_mock: bool = False,
    ) -> BatchPushResponse:
        cfg = self.load_config()
        webhook_url = cfg.webhook_url.strip() if cfg.webhook_url else ""
        if not webhook_url and not force_mock:
            return BatchPushResponse(
                success=False,
                total=0,
                success_count=0,
                failed_count=0,
                results=[],
            )

        results = []
        success_cnt = 0
        failed_cnt = 0

        if not insight_ids:
            # Query insights that have not been pushed or top recent insights
            q = select(Insight.id).order_by(desc(Insight.created_at)).limit(50)
            rows = (await self.session.execute(q)).scalars().all()
            insight_ids = list(rows)

        for i_id in insight_ids:
            res = await self.push_single_insight(i_id, operator=operator, force_mock=force_mock)
            results.append(res)
            if res.success:
                success_cnt += 1
            else:
                failed_cnt += 1

        return BatchPushResponse(
            success=failed_cnt == 0,
            total=len(insight_ids),
            total_attempted=len(insight_ids),
            success_count=success_cnt,
            failed_count=failed_cnt,
            results=results,
        )

    # Alias for CLI and API consistency
    batch_push_insights = push_batch_insights

    async def get_pushed_insights_list(
        self,
        date_preset: str = "all",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        status_filter: Optional[str] = None,  # success | failed | not_pushed
        module_filter: Optional[str] = None,
        search_query: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[PushedInsightItem], int]:
        """
        Retrieves insights formatted for the '需求洞察推送' list view.
        Enriches each insight with latest push status, total push count, and complete push history.
        """
        # 1. Fetch insights
        q = select(Insight)
        if module_filter and module_filter.strip().lower() not in ("all", "全部", ""):
            q = q.where(Insight.module.like(f"%{module_filter.strip()}%"))
        if search_query and search_query.strip():
            term = f"%{search_query.strip()}%"
            q = q.where(
                (Insight.summary.like(term))
                | (Insight.description.like(term))
                | (Insight.module.like(term))
            )

        q = q.order_by(desc(Insight.created_at))
        all_insights = (await self.session.execute(q)).scalars().all()

        if not all_insights:
            return [], 0

        ins_ids = [i.id for i in all_insights]

        # 2. Fetch all push records for these insights
        rec_q = (
            select(InsightPushRecord)
            .where(InsightPushRecord.insight_id.in_(ins_ids))
            .order_by(desc(InsightPushRecord.created_at))
        )
        records = (await self.session.execute(rec_q)).scalars().all()

        record_map: dict[str, list[InsightPushRecord]] = {}
        for r in records:
            if r.insight_id not in record_map:
                record_map[r.insight_id] = []
            record_map[r.insight_id].append(r)

        # 3. Resolve date range filtering if specified
        dt_start, dt_end = self._resolve_date_range(date_preset, start_date, end_date)

        type_mapping = {
            "feature_request": "功能需求",
            "issue": "产品缺陷",
            "inquiry": "用户咨询",
            "praise": "体验好评",
        }
        sev_mapping = {
            "blocker": "🚨 致命阻塞",
            "major": "⚠️ 严重故障",
            "minor": "📌 一般缺陷",
            "trivial": "🌱 轻微建议",
        }

        items: list[PushedInsightItem] = []
        for ins in all_insights:
            history = record_map.get(ins.id, [])
            push_cnt = len(history)
            latest_rec = history[0] if history else None

            last_push_time = latest_rec.created_at.strftime("%Y-%m-%d %H:%M:%S") if latest_rec else None
            p_status = latest_rec.state if latest_rec else "pending"

            # Apply date range filter based on latest push or insight creation
            if dt_start and dt_end:
                check_dt = latest_rec.created_at if latest_rec else ins.created_at
                if not (dt_start <= check_dt <= dt_end):
                    continue

            # Apply status filter
            if status_filter and status_filter.strip().lower() not in ("all", "全部", ""):
                sf = status_filter.strip().lower()
                if sf in ("pending", "not_pushed"):
                    if p_status not in ("pending", "not_pushed"):
                        continue
                elif p_status != sf:
                    continue

            clean_title = ins.summary.split("]")[-1].strip() if "]" in ins.summary else ins.summary.strip()
            module_zh = TagManager.format_tag_display(ins.module) if ins.module else "综合体验"

            history_items = [
                PushHistoryRecordItem(
                    id=h.id,
                    insight_id=h.insight_id,
                    target_platform=h.target_platform,
                    webhook_url=h.webhook_url,
                    state=h.state,
                    status_code=h.status_code,
                    error_message=h.error_message,
                    operator=h.operator,
                    created_at=h.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                )
                for h in history
            ]

            raw_tags = list(ins.tags_json or [])
            formatted_tags = [f"#{TagManager.format_tag_display(t).lstrip('#')}" for t in raw_tags if t]

            items.append(
                PushedInsightItem(
                    id=ins.id,
                    insight_id=ins.id,
                    title=clean_title,
                    module=ins.module,
                    module_zh=module_zh,
                    tags=formatted_tags,
                    insight_type=ins.insight_type,
                    type_zh=type_mapping.get(ins.insight_type, "功能需求"),
                    severity=ins.severity,
                    severity_zh=sev_mapping.get(ins.severity, "📌 一般缺陷"),
                    priority=ins.priority or "P1",
                    fact_5w1h=FeishuBitableClient.format_5w1h_multiline(ins.description or ins.summary),
                    facts_zh=FeishuBitableClient.format_5w1h_multiline(ins.description or ins.summary),
                    last_pushed_at=last_push_time,
                    last_operator=history[0].operator if history else None,
                    push_status=p_status,
                    push_count=push_cnt,
                    push_history=history_items,
                    drilldown_uri=f"chatinsight://insights/{ins.id}",
                    created_at=ins.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                )
            )

        total_count = len(items)
        paginated_items = items[offset : offset + limit]
        return paginated_items, total_count

    async def get_push_stats(
        self,
        date_preset: str = "all",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> BitablePushStats:
        """Calculates push volume, attempts, success and failure counts in the given window."""
        dt_start, dt_end = self._resolve_date_range(date_preset, start_date, end_date)

        # 1. Total insights in date window
        ins_q = select(func.count(Insight.id))
        if dt_start and dt_end:
            ins_q = ins_q.where(Insight.created_at.between(dt_start, dt_end))
        total_insights = (await self.session.execute(ins_q)).scalar_one_or_none() or 0

        # 2. Push records in date window
        rec_q = select(InsightPushRecord)
        if dt_start and dt_end:
            rec_q = rec_q.where(InsightPushRecord.created_at.between(dt_start, dt_end))

        records = (await self.session.execute(rec_q)).scalars().all()

        total_attempts = len(records)
        success_cnt = sum(1 for r in records if r.state == "success")
        failed_cnt = sum(1 for r in records if r.state == "failed")
        success_rate = round((success_cnt / total_attempts * 100.0), 1) if total_attempts > 0 else 0.0

        return BitablePushStats(
            total_pushed_insights=total_insights,
            total_push_attempts=total_attempts,
            success_count=success_cnt,
            failed_count=failed_cnt,
            success_rate=success_rate,
        )

    async def purge_push_records(self) -> int:
        """Purges all push records from the database, resetting all insights to pending state."""
        del_stmt = delete(InsightPushRecord)
        res = await self.session.execute(del_stmt)
        await self.session.commit()
        return res.rowcount or 0

    async def get_recent_pushed_insights(self, limit: int = 8) -> list[PushedInsightItem]:
        """Fetches latest pushed insights for the Overview Tab."""
        items, _ = await self.get_pushed_insights_list(
            date_preset="all",
            limit=limit,
            offset=0,
        )
        return items

    @staticmethod
    def _resolve_date_range(
        date_preset: str,
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> tuple[Optional[datetime], Optional[datetime]]:
        now = datetime.now()
        preset = (date_preset or "all").lower()

        if preset in ("all", "all_time"):
            return None, None
        elif preset in ("today", "1d", "day"):
            dt_start = datetime.combine(now.date(), datetime.min.time())
            dt_end = datetime.combine(now.date(), datetime.max.time())
            return dt_start, dt_end
        elif preset in ("7d", "last_7_days", "week"):
            dt_end = datetime.combine(now.date(), datetime.max.time())
            dt_start = dt_end - timedelta(days=7)
            return dt_start, dt_end
        elif preset in ("30d", "last_30_days", "month"):
            dt_end = datetime.combine(now.date(), datetime.max.time())
            dt_start = dt_end - timedelta(days=30)
            return dt_start, dt_end
        elif preset == "custom" and start_date and end_date:
            try:
                s_d = datetime.strptime(start_date, "%Y-%m-%d")
                e_d = datetime.strptime(end_date, "%Y-%m-%d")
                dt_start = datetime.combine(s_d.date(), datetime.min.time())
                dt_end = datetime.combine(e_d.date(), datetime.max.time())
                return dt_start, dt_end
            except Exception:
                return None, None
        return None, None

from datetime import datetime
from typing import Any, Optional
import httpx
from pydantic import BaseModel

from packages.abc_sync.contracts import ABCFeedbackPayload, ABCSyncResult
from packages.persistence.models import Insight, Topic


class ABCSyncClient:
    """Client for synchronizing Topics to ABC User Feedback platform or local export sink."""

    def __init__(self, base_url: str | None = None, api_token: str | None = None):
        self.base_url = (base_url or "http://localhost:8080").rstrip("/")
        self.api_token = api_token

    def format_topic_payload(self, topic: Topic, insights: list[Insight]) -> ABCFeedbackPayload:
        evidence_urls: list[str] = []
        for ins in insights:
            evidence_urls.append(f"chatinsight://insights/{ins.id}")

        detail_text = f"{topic.summary}\n\n【聚合用户反馈 ({topic.feedback_count} 次)】\n"
        for idx, ins in enumerate(insights[:5], start=1):
            detail_text += f"{idx}. [{ins.severity.upper()}] {ins.summary} (状态: {ins.status_in_chat})\n"

        return ABCFeedbackPayload(
            external_id=topic.id,
            channel_id="wechat_community",
            title=topic.title,
            content=detail_text.strip(),
            category=topic.module,
            severity=topic.severity,
            status="open" if topic.status == "open" else "in_progress",
            tags=topic.tags_json or [],
            feedback_count=topic.feedback_count,
            unique_users_count=topic.unique_users_count,
            evidence_urls=evidence_urls,
            reported_at=topic.first_seen_at.isoformat(),
            last_updated_at=topic.last_seen_at.isoformat(),
            metadata={
                "sub_module": topic.sub_module,
                "chatinsight_sync_time": datetime.now().isoformat(),
            },
        )

    async def push_payload(self, payload: ABCFeedbackPayload) -> tuple[bool, str, Optional[str]]:
        """Sends payload to ABC API or returns simulated success if offline."""
        if not self.api_token or "localhost" in self.base_url:
            # Simulated local success for development / standalone mode
            return True, f"abc_{payload.external_id[:12]}", None

        headers = {"Authorization": f"Bearer {self.api_token}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                res = await client.post(
                    f"{self.base_url}/api/v1/feedbacks",
                    json=payload.model_dump(),
                    headers=headers,
                )
                if res.status_code in (200, 201):
                    data = res.json()
                    return True, data.get("id", f"abc_{payload.external_id[:12]}"), None
                return False, "", f"ABC API returned {res.status_code}: {res.text[:300]}"
            except Exception as exc:
                return False, "", str(exc)

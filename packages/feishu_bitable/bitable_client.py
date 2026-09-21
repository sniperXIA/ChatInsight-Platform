import json
import logging
import re
from typing import Any, Optional
import httpx

from packages.feishu_bitable.contracts import BitablePushResponse
from packages.insights.tag_manager import TagManager
from packages.persistence.models import Insight

logger = logging.getLogger(__name__)


class FeishuBitableClient:
    """Client for formatting and dispatching Insight records to Feishu Bitable Webhook."""

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    @staticmethod
    def format_5w1h_multiline(text: str) -> str:
        """
        Formats 5W1H factual symptom description so that each sub-point
        (e.g., 【场景/位置】、[具体现象]、[影响程度]、[社群进展])
        appears cleanly on its own separate line.
        """
        if not text:
            return ""
        clean_text = text.strip()

        # Match bracketed sub-points like [场景/位置], 【具体现象】, etc.
        pattern = r'([\[【][^\]】]+[\]】][\s:：]*)'
        parts = re.split(pattern, clean_text)

        if len(parts) > 1:
            lines = []
            current_label = ""
            for p in parts:
                if not p:
                    continue
                if re.match(r'^[\[【][^\]】]+[\]】][\s:：]*$', p):
                    label_name = re.sub(r'[\[\]【】\s:：]', '', p)
                    current_label = f"【{label_name}】"
                else:
                    content = p.strip().rstrip('；;，,')
                    if current_label:
                        lines.append(f"{current_label} {content}")
                        current_label = ""
                    else:
                        if content:
                            lines.append(content)
            if lines:
                return "\n".join(lines)

        # Fallback 1: if numbered list items e.g., 1. xxx 2. yyy
        num_pattern = r'(?=(\d+[\.、\)]\s*))'
        num_parts = re.split(num_pattern, clean_text)
        if len(num_parts) > 1:
            lines = [np.strip().rstrip('；;') for np in num_parts if np.strip()]
            if lines:
                return "\n".join(lines)

        # Fallback 2: if plain narrative text with multiple sentences (split by Chinese full stop)
        if "。" in clean_text:
            sentences = [s.strip() for s in clean_text.split("。") if s.strip()]
            if len(sentences) > 1:
                return "。\n".join(sentences) + "。"

        return clean_text

    @staticmethod
    def format_insight_payload(insight: Insight) -> dict[str, Any]:
        """
        Formats insight into exact 6 Feishu Bitable fields:
        1. 功能模块 (Text): e.g. "界面与显示 · 字体与清晰度"
        2. 内容标签 (Multi-select): e.g. ["#配置与音色", "#伴奏风格包", "#扩展卡识别"]
        3. 反馈类型 (Text): e.g. "功能需求" / "产品缺陷" / "用户咨询"
        4. 洞察标题 (Text): clean full title
        5. 5W1H事实 (Text): structured symptom & description (each point on its own line)
        6. 严重级别 (Text): e.g. "严重故障" / "致命阻塞" / "一般缺陷" / "轻微建议"
        """
        # 1. 功能模块
        module_zh = TagManager.format_tag_display(insight.module) if insight.module else "综合体验"

        # 2. 内容标签 (format each as #标签)
        raw_tags = list(insight.tags_json or [])
        if not raw_tags and insight.module:
            raw_tags = [insight.module]
        content_tags = []
        for t in raw_tags:
            zh_tag = TagManager.format_tag_display(t)
            cleaned = zh_tag.strip().lstrip("#")
            if cleaned and f"#{cleaned}" not in content_tags:
                content_tags.append(f"#{cleaned}")
        if not content_tags:
            content_tags = [f"#{module_zh.split('·')[-1].strip()}"]

        # 3. 反馈类型
        type_mapping = {
            "feature_request": "功能需求",
            "issue": "产品缺陷",
            "inquiry": "用户咨询",
            "praise": "体验好评",
        }
        feedback_type = type_mapping.get(insight.insight_type, "功能需求")

        # 4. 洞察标题 (full cleaned summary)
        title = insight.summary.split("]")[-1].strip() if "]" in insight.summary else insight.summary.strip()

        # 5. 5W1H事实 (description / 5W1H structured fact with each point on its own line)
        raw_fact = (insight.description or insight.summary).strip()
        fact_5w1h = FeishuBitableClient.format_5w1h_multiline(raw_fact)

        # 6. 严重级别
        sev_mapping = {
            "blocker": "致命阻塞",
            "major": "严重故障",
            "minor": "一般缺陷",
            "trivial": "轻微建议",
        }
        severity_zh = sev_mapping.get(insight.severity, "一般缺陷")

        record_fields = {
            "功能模块": module_zh,
            "内容标签": content_tags,
            "反馈类型": feedback_type,
            "洞察标题": title,
            "5W1H事实": fact_5w1h,
            "严重级别": severity_zh,
        }
        if getattr(insight, "device_model", None):
            record_fields["设备机型"] = insight.device_model

        # Provide both flat root fields and nested `fields` object for 100% Feishu Bitable compatibility
        payload = {
            **record_fields,
            "fields": record_fields,
            "insight_id": insight.id,
            "device_model": getattr(insight, "device_model", "") or "",
            "priority": insight.priority or "P1",
            "drilldown_uri": f"chatinsight://insights/{insight.id}",
        }
        return payload

    async def push_to_webhook(
        self,
        webhook_url: str,
        payload: dict[str, Any],
        bearer_token: Optional[str] = None,
        force_mock: bool = False,
    ) -> tuple[bool, int, str, Optional[str]]:
        """
        Dispatches payload to the specified Feishu Bitable webhook.
        Returns: (success: bool, status_code: int, response_body: str, error_message: str | None)
        """
        if not webhook_url or not webhook_url.strip():
            if force_mock:
                return True, 200, json.dumps({"code": 0, "msg": "success (mock dispatch)", "record_id": f"rec_{payload.get('insight_id', 'test')[:8]}"}), None
            return False, 400, "", "未配置飞书多维表格 Webhook 地址，请在右上角【飞书多维表格 Webhook 设置】中配置有效地址后再执行推送。"

        if force_mock or "mock" in webhook_url:
            # Simulated push success for mock environments
            return True, 200, json.dumps({"code": 0, "msg": "success (mock dispatch)", "record_id": f"rec_{payload.get('insight_id', 'test')[:8]}"}), None

        headers = {"Content-Type": "application/json; charset=utf-8"}
        if bearer_token and bearer_token.strip():
            headers["Authorization"] = f"Bearer {bearer_token.strip()}"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(webhook_url, json=payload, headers=headers)
                status_code = resp.status_code
                body_text = resp.text
                if 200 <= status_code < 300:
                    return True, status_code, body_text, None
                elif status_code in (401, 403):
                    return False, status_code, body_text, f"飞书多维表格凭证校验失败 (HTTP {status_code})：请检查是否在飞书自动化设置中开启了「凭证校验 (Bearer token)」，并确认填写的 Bearer token 是否一致。"
                else:
                    return False, status_code, body_text, f"飞书多维表格 Webhook 返回状态码 {status_code}: {body_text[:200]}"
            except Exception as exc:
                return False, 500, "", f"推送连接异常: {str(exc)}"

    async def test_connectivity(
        self,
        webhook_url: str,
        bearer_token: Optional[str] = None,
    ) -> tuple[bool, int, str]:
        """Sends a verification probe record to test webhook connectivity and provide sample schema."""
        if not webhook_url or not webhook_url.strip():
            return False, 400, "飞书多维表格 Webhook 地址不能为空"

        # Provide a rich, realistic sample record with all 6 business fields
        # This enables Feishu Bitable's "通过发送请求设置" to automatically identify and parse all fields
        sample_tags = [
            "#界面与显示",
            "#字体与清晰度",
            "#车载使用场景",
            "#夜间模式",
            "#大字体排版",
            "#高对比度显示",
            "#无障碍视力关怀",
            "#设置项与个性化",
            "#强光抗眩光优化",
            "#易用性体验提升",
        ]
        sample_5w1h = (
            "【场景/位置】 用户在夜间或车载颠簸弱光环境下使用 App 进行吉他演奏与参数调谐\n"
            "【具体现象】 当前谱面及参数字号偏小、界面缺乏高对比度深色/大字模式，导致弱光下难以辨识\n"
            "【影响程度】 造成演奏阅读中断与误操作，影响中老年及弱视力用户的交互体验\n"
            "【环境/触发条件】 弱光、车载、运动震动场景下高频发生\n"
            "【社群进展】 多位社群资深用户反馈并提报，官方体验组已立项推进界面易用性专项优化"
        )
        sample_payload = {
            "功能模块": "界面与显示 · 字体与清晰度",
            "内容标签": sample_tags,
            "反馈类型": "功能需求",
            "洞察标题": "建议App在车载弱光场景下增加大字体与高对比度模式",
            "5W1H事实": sample_5w1h,
            "严重级别": "一般缺陷",
            "fields": {
                "功能模块": "界面与显示 · 字体与清晰度",
                "内容标签": sample_tags,
                "反馈类型": "功能需求",
                "洞察标题": "建议App在车载弱光场景下增加大字体与高对比度模式",
                "5W1H事实": sample_5w1h,
                "严重级别": "一般缺陷",
            },
            "is_sample_probe": True,
        }

        success, status_code, body, err = await self.push_to_webhook(
            webhook_url=webhook_url,
            payload=sample_payload,
            bearer_token=bearer_token,
        )
        if success:
            return True, status_code, "Webhook 连通性测试成功！已向飞书发送包含 6 大业务字段的标准示例数据，您现在可以在飞书多维表格界面的「输出设置」中点击【生成】按钮自动生成字段！"
        return False, status_code, err or "连通性测试失败"

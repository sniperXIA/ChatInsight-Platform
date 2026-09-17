import base64
from datetime import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
from typing import Any, Optional
import httpx

from packages.analytics.contracts import FeishuConfig, FeishuPushResponse, VoCReportOutput

CONFIG_PATH = Path("config/feishu_settings.json")


class FeishuService:
    """
    Feishu Custom Robot Webhook Integration Service.
    Supports HMAC-SHA256 signature, connectivity tests, and rendering
    VoC Business Reports into elegant Feishu Interactive Cards.
    """

    def __init__(self, config_path: Path | str = CONFIG_PATH):
        self.config_path = Path(config_path)

    def load_config(self) -> FeishuConfig:
        """Loads Feishu configuration from disk."""
        if not self.config_path.exists():
            return FeishuConfig()
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return FeishuConfig(**data)
        except Exception:
            return FeishuConfig()

    def save_config(self, config: FeishuConfig) -> None:
        """Saves Feishu configuration to disk."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        # If new secret is masked (e.g. starts with ***), preserve existing
        if config.secret and config.secret.startswith("***"):
            old = self.load_config()
            config.secret = old.secret
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config.model_dump(), f, ensure_ascii=False, indent=2)

    def _generate_sign(self, secret: str, timestamp: str) -> str:
        """Generates HMAC-SHA256 signature required by Feishu Bot security settings."""
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        return base64.b64encode(hmac_code).decode("utf-8")

    async def send_test_message(self, webhook_url: Optional[str] = None, secret: Optional[str] = None) -> FeishuPushResponse:
        """Sends a connectivity verification card to Feishu."""
        cfg = self.load_config()
        url = webhook_url or cfg.webhook_url
        sec = secret if secret is not None else cfg.secret

        if not url or not url.strip():
            return FeishuPushResponse(success=False, status_code=400, message="飞书 Webhook 地址未配置，请先输入有效的机器人群聊 Webhook URL")

        timestamp = str(int(time.time()))
        payload: dict[str, Any] = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True, "enable_forward": True},
                "header": {
                    "title": {"tag": "plain_text", "content": "✅ ChatInsight 飞书机器人连通性验证成功"},
                    "template": "green",
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": (
                                "**🎉 恭喜！ChatInsight 平台与当前飞书群机器人已成功建立双向通信。**\n\n"
                                f"• **触发时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                                f"• **加签校验**: {'已开启 HMAC-SHA256 安全签名' if sec else '未设置加签密钥（标准模式）'}\n"
                                "• **功能支持**: 支持实时运营概况大盘、业务标签异动预警、高紧迫度洞察及定时周报推送。\n\n"
                                "*您现在可以在 ChatInsight 控制台点击【推送本期报告至飞书群】，随时将最新 VoC 报告同步至本群。*"
                            ),
                        },
                    },
                    {"tag": "hr"},
                    {
                        "tag": "note",
                        "elements": [{"tag": "plain_text", "content": "ChatInsight 多模态社群事实沉淀与 VoC 分析平台"}],
                    },
                ],
            },
        }

        if sec and sec.strip():
            payload["timestamp"] = timestamp
            payload["sign"] = self._generate_sign(sec.strip(), timestamp)

        return await self._post_payload(url.strip(), payload)

    async def send_voc_interactive_card(
        self,
        report: VoCReportOutput,
        custom_note: Optional[str] = None,
        base_web_url: str = "http://localhost:8000",
    ) -> FeishuPushResponse:
        """Transforms a VoCReportOutput into a rich Feishu Interactive Card and dispatches it."""
        cfg = self.load_config()
        if not cfg.webhook_url or not cfg.webhook_url.strip():
            return FeishuPushResponse(success=False, status_code=400, message="飞书 Webhook 地址未配置，请先前往设置填写")

        op = report.operational_overview
        hv = report.high_value_content
        timestamp = str(int(time.time()))

        # 1. Format Category Dynamics Lines
        cat_lines = []
        for c in hv.category_dynamics[:4]:
            badge = "🔴 飙升预警" if c.alert_level == "critical" else ("🟡 上升关注" if c.alert_level == "warning" else "🟢 平稳")
            change_str = f"({c.change_pct:+.1f}%)" if c.change_pct is not None else ""
            cat_lines.append(f"• **{c.category_zh}**: {c.total_count} 条 {change_str} `[{badge}]`")
        cat_content = "\n".join(cat_lines) if cat_lines else "• 暂无显著模块分类聚集"

        # 2. Format Top Critical Insights
        crit_lines = []
        for idx, ins in enumerate(hv.critical_insights[:3], 1):
            quote_text = f" > 原声: *{ins.verbatim_quote}*" if ins.verbatim_quote else ""
            crit_lines.append(
                f"**{idx}. [{ins.severity_zh}] {ins.title}**\n"
                f"• **现象**: {ins.symptom[:70]}...\n"
                f"{quote_text}\n"
                f"• **改进方向**: {ins.actionable_direction or '建议研发建单跟踪'}\n"
            )
        crit_content = "\n".join(crit_lines) if crit_lines else "• 当前周期未发现 P0/P1 致命故障"

        # 3. Format Key Recommendations
        rec_lines = "\n".join(f"{idx}. {r}" for idx, r in enumerate(hv.key_recommendations[:3], 1))

        # Build Interactive Card elements
        elements: list[dict[str, Any]] = [
            # Block 1: Operational Overview Summary Box
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"📢 **运营综述**: {op.brief_summary}",
                },
            },
            {"tag": "hr"},
            # Block 2: Four-Grid KPI Metrics
            {
                "tag": "div",
                "fields": [
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": f"💬 **处理消息**\n**{op.total_messages:,}** 条\n(日均 {op.daily_avg_messages:,} 条)",
                        },
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": f"🎯 **聚类话题**\n**{op.total_episodes}** 切片话题\n({op.total_topics} 聚合主题)",
                        },
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": f"💡 **结构化洞察**\n**{op.total_insights}** 条\n(🚨 严重度 P1/P2: {len(hv.critical_insights)})",
                        },
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": f"⚡ **模型算力消耗**\n**{op.token_usage.total_tokens:,}** Tokens\n({op.token_usage.analysis_runs_count} 次大模型调用)",
                        },
                    },
                ],
            },
            {"tag": "hr"},
            # Block 3: Category Dynamics & Alert Status
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"📈 **业务标签分布与异动预警 (Category Dynamics)**:\n{cat_content}",
                },
            },
            {"tag": "hr"},
            # Block 4: Critical/Blocker Actionable Insights
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"🚨 **高紧迫度重点缺陷与需求 (Top Actionable Issues)**:\n{crit_content}",
                },
            },
            {"tag": "hr"},
            # Block 5: Action Recommendations
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"💡 **管理层战略与落地行动建议**:\n{rec_lines}",
                },
            },
            # Block 6: Action Buttons
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🖥️ 查看平台完整报告"},
                        "type": "primary",
                        "url": f"{base_web_url}/?tab=reports",
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🤖 启动智能研究助手"},
                        "type": "default",
                        "url": f"{base_web_url}/?tab=assistant",
                    },
                ],
            },
            # Block 7: Footer Note
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": f"由 ChatInsight 全景运营引擎生成 • 统计周期: {op.start_date} ~ {op.end_date} (共 {op.days_count} 天) • 飞书推送时间: {time.strftime('%Y-%m-%d %H:%M')}",
                    }
                ],
            },
        ]

        if custom_note:
            elements.insert(0, {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"🔔 **推送附言**: {custom_note}"},
            })

        card_payload: dict[str, Any] = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True, "enable_forward": True},
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"📊 {report.period_label}",
                    },
                    "template": "indigo",
                },
                "elements": elements,
            },
        }

        if cfg.secret and cfg.secret.strip():
            card_payload["timestamp"] = timestamp
            card_payload["sign"] = self._generate_sign(cfg.secret.strip(), timestamp)

        resp = await self._post_payload(cfg.webhook_url.strip(), card_payload)
        # Update push history
        cfg.last_push_at = datetime.now().isoformat()
        cfg.last_push_status = "success" if resp.success else f"failed: {resp.message}"
        self.save_config(cfg)

        return resp

    async def _post_payload(self, url: str, payload: dict[str, Any]) -> FeishuPushResponse:
        """Dispatches HTTP POST to Feishu and handles status codes and error diagnostics."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
                status_code = res.status_code
                try:
                    data = res.json()
                except Exception:
                    data = {"text": res.text}

                # Feishu returns HTTP 200 with code != 0 on error
                code = data.get("code")
                msg = data.get("msg") or data.get("message") or ""
                log_id = res.headers.get("x-tt-logid")

                if status_code == 200 and (code == 0 or code is None):
                    return FeishuPushResponse(
                        success=True,
                        status_code=200,
                        message="推送成功，飞书群已接收到业务卡片消息",
                        feishu_log_id=log_id,
                    )
                else:
                    return FeishuPushResponse(
                        success=False,
                        status_code=status_code,
                        message=f"飞书返回错误 (code: {code}): {msg or res.text}",
                        feishu_log_id=log_id,
                    )
        except Exception as e:
            return FeishuPushResponse(
                success=False,
                status_code=500,
                message=f"网络请求失败或 Webhook 端点不可达: {str(e)}",
            )

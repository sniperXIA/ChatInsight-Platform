import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from sqlalchemy import select

# Fix Windows console UTF-8 printing
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from packages.feishu_bitable.feishu_bitable_service import FeishuBitableService
from packages.analytics.report_generator import ReportGenerator
from packages.clustering.cluster_manager import ClusterManager
from packages.importers.batch_importer import BatchImporter
from packages.importers.scanner import DirectoryScanner
from packages.insights.episode_segmenter import EpisodeSegmenter
from packages.insights.insight_extractor import InsightExtractor
from packages.media_pipeline.image_pipeline import ImageEnrichmentPipeline
from packages.media_pipeline.video_pipeline import VideoEnrichmentPipeline
from packages.persistence.db import create_all_tables, get_session_context, init_db_engine
from packages.persistence.models import (
    Conversation,
    Episode,
    Insight,
    MediaAsset,
    MediaEnrichment,
    Topic,
)
from packages.search.contracts import SearchFilter
from packages.search.hybrid_search import HybridSearchEngine
from packages.search.research_assistant import ResearchAssistant


def load_env():
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()


async def cmd_scan(args):
    load_env()
    scanner = DirectoryScanner()
    report, batches = scanner.scan_root(
        root_path=args.path,
        target_date=args.date,
        target_group=args.group,
    )
    print(f"\n================ 预检扫描报告 (Scan ID: {report.scan_id}) ================")
    print(f"数据源路径: {report.source_root_path}")
    print(f"识别群聊数: {report.summary.conversation_count}")
    print(f"识别消息数: {report.summary.message_drafts}")
    print(f"媒体文件数: {report.summary.media_files}")
    print(f"明确关联媒体: {report.summary.explicit_media_links}")
    print(f"待确认歧义媒体: {report.summary.unresolved_image_placeholders}")
    print(f"阻断性错误数: {len(report.blocking_issues)}")
    print(f"警告提示数: {len(report.warnings)}")

    if report.blocking_issues:
        print("\n[阻断性问题]:")
        for b in report.blocking_issues:
            print(f"  - [{b.code}] {b.source_file}: {b.message}")

    print("\n扫描批次详情:")
    for b in report.batches:
        print(f"  * {b['date_str']} | {b['conversation_name']} | 消息: {b['message_count']} | 媒体: {b['media_count']}")


async def cmd_import(args):
    load_env()
    init_db_engine()
    await create_all_tables()

    scanner = DirectoryScanner()
    report, batches = scanner.scan_root(
        root_path=args.path,
        target_date=args.date,
        target_group=args.group,
    )

    if report.blocking_issues:
        print(f"[错误] 存在 {len(report.blocking_issues)} 个阻断性问题，导入终止。")
        return

    print(f"\n开始导入 {len(batches)} 个群聊批次...")
    async with get_session_context() as session:
        importer = BatchImporter(session)
        for idx, batch in enumerate(batches, start=1):
            stats = await importer.import_parsed_batch(batch, root_path=args.path)
            status_text = "已跳过(已存在且一致)" if stats.already_completed else f"新增消息 {stats.messages_inserted} 条, 媒体 {stats.media_inserted} 个"
            print(f"[{idx}/{len(batches)}] {batch.date_str} - {batch.conversation_name}: {status_text} ({stats.duration_ms:.1f}ms)")
        await session.commit()

    print("\n所有批次导入完成！")


async def cmd_analyze_media(args):
    load_env()
    init_db_engine()
    await create_all_tables()

    async with get_session_context() as session:
        if args.id:
            stmt = select(MediaAsset).where(MediaAsset.id == args.id)
        else:
            stmt = select(MediaAsset).limit(args.limit)

        res = await session.execute(stmt)
        media_list = res.scalars().all()

        if not media_list:
            print("未找到需要分析的媒体资产。")
            return

        print(f"\n开始多模态解析 {len(media_list)} 个媒体资产 (Mock={args.mock})...")
        for idx, media in enumerate(media_list, start=1):
            try:
                if media.kind == "image":
                    pipe = ImageEnrichmentPipeline(session)
                    output, enrichment, cached = await pipe.analyze_image_asset(
                        media_id=media.id,
                        force_mock=args.mock,
                        model_override=args.model,
                    )
                    cached_tag = " [Cache命中]" if cached else ""
                    print(f"[{idx}/{len(media_list)}] 图片 {media.id[:8]}... 解析成功{cached_tag}: 摘要: {output.summary}")
                elif media.kind == "video":
                    pipe = VideoEnrichmentPipeline(session)
                    output, enrichment, cached = await pipe.analyze_video_asset(
                        media_id=media.id,
                        force_mock=args.mock,
                        model_override=args.model,
                    )
                    cached_tag = " [Cache命中]" if cached else ""
                    print(f"[{idx}/{len(media_list)}] 视频 {media.id[:8]}... 解析成功{cached_tag}: 摘要: {output.summary}")
            except Exception as e:
                print(f"[{idx}/{len(media_list)}] 媒体 {media.id[:8]} 解析失败: {str(e)}")

        await session.commit()
        print("\n媒体多模态解析完成！")


async def cmd_segment_episodes(args):
    load_env()
    init_db_engine()
    await create_all_tables()

    async with get_session_context() as session:
        if args.conv_id:
            stmt = select(Conversation).where(Conversation.id == args.conv_id)
        else:
            stmt = select(Conversation).limit(args.limit)

        conversations = (await session.execute(stmt)).scalars().all()
        if not conversations:
            print("未找到群聊记录，请先执行 import 命令导入数据。")
            return

        print(f"\n开始对 {len(conversations)} 个群聊执行 Episode 话题切分...")
        segmenter = EpisodeSegmenter(session)
        total_episodes = 0

        for conv in conversations:
            episodes = await segmenter.segment_conversation(
                conversation_id=conv.id,
                time_gap_minutes=args.gap,
                force_mock=args.mock,
            )
            total_episodes += len(episodes)
            print(f"  * 群聊 [{conv.display_name}] 切分出 {len(episodes)} 个话题 Episode:")
            for ep in episodes:
                clean_title = ep.title.replace("\u2005", " ")
                print(f"    - [{ep.category_hint}] {clean_title} ({ep.message_count} 条消息)")

        await session.commit()
        print(f"\n切分完成！共生成 {total_episodes} 个 Episode。")


async def cmd_extract_insights(args):
    load_env()
    init_db_engine()
    await create_all_tables()

    async with get_session_context() as session:
        if args.ep_id:
            stmt = select(Episode).where(Episode.id == args.ep_id)
        else:
            stmt = select(Episode).limit(args.limit)

        episodes = (await session.execute(stmt)).scalars().all()
        if not episodes:
            print("未找到 Episode 记录，请先执行 segment-episodes 命令。")
            return

        print(f"\n开始从 {len(episodes)} 个 Episode 中提炼产品洞察 (Mock={args.mock})...")
        extractor = InsightExtractor(session)
        total_insights = 0

        for ep in episodes:
            results = await extractor.extract_insights_from_episode(
                episode_id=ep.id,
                force_mock=args.mock,
                model_override=args.model,
            )
            total_insights += len(results)
            clean_title = ep.title.replace("\u2005", " ")
            print(f"  * Episode: {clean_title} -> 提炼 {len(results)} 条洞察:")
            for ins, check in results:
                print(f"    - [{ins.insight_type.upper()}] {ins.module}: {ins.summary}")
                print(f"      状态: {ins.status_in_chat} | 事实分: {ins.factual_score:.2f} | 建议: {check.action_recommendation}")

        await session.commit()
        print(f"\n洞察提炼完成！共生成 {total_insights} 条结构化 Insight 记录。")


async def cmd_cluster_insights(args):
    load_env()
    init_db_engine()
    await create_all_tables()

    async with get_session_context() as session:
        if args.ins_id:
            stmt = select(Insight).where(Insight.id == args.ins_id)
        else:
            stmt = select(Insight).limit(args.limit)

        insights = (await session.execute(stmt)).scalars().all()
        if not insights:
            print("未找到 Insight 记录，请先执行 extract-insights 命令。")
            return

        print(f"\n开始对 {len(insights)} 条洞察执行两阶段聚类去重 (Mock={args.mock})...")
        manager = ClusterManager(session)
        for idx, ins in enumerate(insights, start=1):
            topic, dec, judge_res = await manager.cluster_insight(
                insight_id=ins.id,
                force_mock=args.mock,
                model_override=args.model,
            )
            dec_text = "【新建主题】" if dec.value == "DISTINCT_ISSUE" else f"【合并至已有主题: {topic.title}】"
            print(f"[{idx}/{len(insights)}] Insight: {ins.summary}")
            print(f"    -> {dec_text} (当前反馈总频次: {topic.feedback_count})")

        await session.commit()
        print("\n两阶段聚类去重完成！")


async def cmd_push_bitable(args):
    load_env()
    init_db_engine()
    await create_all_tables()

    async with get_session_context() as session:
        service = FeishuBitableService(session)
        ins_id = getattr(args, "insight_id", None) or getattr(args, "ins_id", None)
        force_mock = getattr(args, "mock", False)
        if ins_id:
            res = await service.push_single_insight(ins_id, operator="cli", force_mock=force_mock)
            if res.success:
                print(f"Insight {ins_id} 成功推送至飞书多维表格！")
            else:
                print(f"Insight {ins_id} 推送失败: {res.message or res.error}")
        else:
            res = await service.push_batch_insights(operator="cli", force_mock=force_mock)
            print(f"\n飞书多维表格批量推送完成: 成功 {res.success_count} 条，失败 {res.failed_count} 条，共处理 {res.total_attempted} 条")
        await session.commit()


cmd_sync_abc = cmd_push_bitable


async def cmd_search(args):
    load_env()
    init_db_engine()
    await create_all_tables()

    async with get_session_context() as session:
        engine = HybridSearchEngine(session)
        res = await engine.search(query=args.query, limit=args.limit)
        print(f"\n=== 混合检索结果: '{args.query}' (耗时: {res.duration_ms:.1f}ms, 命中: {res.total_hits}条) ===")
        for idx, r in enumerate(res.results, start=1):
            print(f"[{idx}] [{r.entity_type.upper()}] (得分: {r.score:.2f}) {r.title}")
            print(f"    摘要: {r.snippet}")
            print(f"    URI: {r.evidence_uri}")


async def cmd_ask_assistant(args):
    load_env()
    init_db_engine()
    await create_all_tables()

    async with get_session_context() as session:
        assistant = ResearchAssistant(session)
        res = await assistant.answer_question(
            question=args.question,
            force_mock=args.mock,
            model_override=args.model,
        )
        print(f"\n=== 🤖 研究助手回答 (置信度: {res.confidence*100:.0f}%) ===")
        print(f"问题: {res.question}\n")
        print(f"回答:\n{res.answer}\n")
        if res.citations:
            print("证据引用:")
            for c in res.citations:
                print(f"  [{c.citation_id}] {c.title} -> {c.evidence_uri}")


async def cmd_generate_report(args):
    load_env()
    init_db_engine()
    await create_all_tables()

    async with get_session_context() as session:
        generator = ReportGenerator(session)
        report = await generator.generate_report(period_label=args.period, force_mock=args.mock)
        md = generator.render_markdown(report)
        print("\n" + md)
        if args.output:
            out_p = Path(args.output)
            out_p.write_text(md, encoding="utf-8")
            print(f"\n报告已导出至: {out_p.resolve()}")


async def cmd_pipeline(args):
    """Executes full automated pipeline from scan/import to VoC report generation."""
    print("==================================================================")
    print("🚀 启动 ChatInsight 全链路自动化处理流水线 (One-Click Pipeline)")
    print("==================================================================")

    # 1. Import
    print("\n[Step 1/6] 扫描并导入聊天记录...")
    await cmd_import(args)

    # 2. Multimodal
    print("\n[Step 2/6] 执行多模态图片/视频解析...")
    await cmd_analyze_media(args)

    # 3. Segment
    print("\n[Step 3/6] 执行对话 Episode 话题切分...")
    await cmd_segment_episodes(args)

    # 4. Insights & Claims
    print("\n[Step 4/6] 提炼结构化洞察与 Claim 事实核验...")
    await cmd_extract_insights(args)

    # 5. Cluster & Dedup
    print("\n[Step 5/6] 执行两阶段聚类去重与知识库沉淀...")
    await cmd_cluster_insights(args)

    # 6. Push to Feishu Bitable
    print("\n[Step 6/6] 批量推送需求洞察至飞书多维表格...")
    await cmd_push_bitable(args)

    print("\n[Final] 生成全周期 VoC 业务洞察报告...")
    await cmd_generate_report(args)

    print("\n==================================================================")
    print("🎉 ChatInsight 全链路自动化流水线执行完毕！")
    print("==================================================================")


def main():
    parser = argparse.ArgumentParser(description="ChatInsight 数据管理、多模态、洞察提炼、知识库与研究助手 CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # scan
    p_scan = subparsers.add_parser("scan", help="扫描并预检聊天目录")
    p_scan.add_argument("--path", "-p", default="D:/玩家群聊天信息2", help="聊天记录根目录")
    p_scan.add_argument("--date", "-d", default=None, help="指定日期 (如 20260820)")
    p_scan.add_argument("--group", "-g", default=None, help="指定群名关键词")

    # import
    p_import = subparsers.add_parser("import", help="正式导入聊天记录至数据库")
    p_import.add_argument("--path", "-p", default="D:/玩家群聊天信息2", help="聊天记录根目录")
    p_import.add_argument("--date", "-d", default=None, help="指定日期 (如 20260820)")
    p_import.add_argument("--group", "-g", default=None, help="指定群名关键词")

    # analyze-media
    p_analyze = subparsers.add_parser("analyze-media", help="执行图片/视频多模态解析")
    p_analyze.add_argument("--id", default=None, help="指定特定媒体资产 ID")
    p_analyze.add_argument("--limit", type=int, default=5, help="批量解析最大数量")
    p_analyze.add_argument("--mock", action="store_true", help="使用 Mock 模式离线测试")
    p_analyze.add_argument("--model", default=None, help="指定模型名称")

    # segment-episodes
    p_seg = subparsers.add_parser("segment-episodes", help="执行对话 Episode 话题切分")
    p_seg.add_argument("--conv-id", default=None, help="指定会话 ID")
    p_seg.add_argument("--limit", type=int, default=10, help="会话处理上限")
    p_seg.add_argument("--gap", type=int, default=25, help="切分时间阈值（分钟）")
    p_seg.add_argument("--mock", action="store_true", help="使用 Mock 模式快速测试")

    # extract-insights
    p_ins = subparsers.add_parser("extract-insights", help="执行 Episode 结构化洞察与 Claim 提炼")
    p_ins.add_argument("--ep-id", default=None, help="指定 Episode ID")
    p_ins.add_argument("--limit", type=int, default=5, help="Episode 处理上限")
    p_ins.add_argument("--mock", action="store_true", help="使用 Mock 模式快速测试")
    p_ins.add_argument("--model", default=None, help="指定模型名称")

    # cluster-insights
    p_clu = subparsers.add_parser("cluster-insights", help="执行洞察两阶段聚类去重与主题沉淀")
    p_clu.add_argument("--ins-id", default=None, help="指定 Insight ID")
    p_clu.add_argument("--limit", type=int, default=10, help="处理洞察上限")
    p_clu.add_argument("--mock", action="store_true", help="使用 Mock 模式快速测试")
    p_clu.add_argument("--model", default=None, help="指定模型名称")

    # push-bitable
    p_bitable = subparsers.add_parser("push-bitable", help="将需求洞察推送至飞书多维表格")
    p_bitable.add_argument("--ins-id", "--insight-id", default=None, help="指定 Insight ID 单条推送")

    # sync-abc (alias for push-bitable)
    p_abc = subparsers.add_parser("sync-abc", help="[兼容命令] 将需求洞察推送至飞书多维表格")
    p_abc.add_argument("--ins-id", "--insight-id", "--topic-id", default=None, help="指定 ID")

    # search
    p_srch = subparsers.add_parser("search", help="执行跨多模态与知识库的混合检索")
    p_srch.add_argument("--query", "-q", required=True, help="检索关键词")
    p_srch.add_argument("--limit", type=int, default=10, help="返回条目上限")

    # ask-assistant
    p_ask = subparsers.add_parser("ask-assistant", help="向研究助手提问产品洞察问题")
    p_ask.add_argument("--question", required=True, help="提问内容")
    p_ask.add_argument("--mock", action="store_true", help="使用 Mock 模式离线测试")
    p_ask.add_argument("--model", default=None, help="指定模型名称")

    # generate-report
    p_rep = subparsers.add_parser("generate-report", help="生成业务洞察与 VoC 报告")
    p_rep.add_argument("--period", default="社群用户反馈洞察分析周报", help="报告周期标题")
    p_rep.add_argument("--mock", action="store_true", help="使用 Mock 模式快速生成")
    p_rep.add_argument("--output", "-o", default=None, help="导出 Markdown 文件路径")

    # pipeline
    p_pipe = subparsers.add_parser("pipeline", help="一键全自动执行全链路流水线 (Import->Multimodal->Insights->Clustering->Sync->Report)")
    p_pipe.add_argument("--path", "-p", default="D:/玩家群聊天信息2", help="聊天记录根目录")
    p_pipe.add_argument("--date", "-d", default=None, help="指定日期 (如 20260820)")
    p_pipe.add_argument("--group", "-g", default=None, help="指定群名关键词")
    p_pipe.add_argument("--limit", type=int, default=5, help="各步骤批次限制")
    p_pipe.add_argument("--gap", type=int, default=25, help="切分时间阈值（分钟）")
    p_pipe.add_argument("--id", default=None, help="指定 ID")
    p_pipe.add_argument("--conv-id", default=None, help="指定会话 ID")
    p_pipe.add_argument("--ep-id", default=None, help="指定 Episode ID")
    p_pipe.add_argument("--ins-id", default=None, help="指定 Insight ID")
    p_pipe.add_argument("--topic-id", default=None, help="指定 Topic ID")
    p_pipe.add_argument("--mock", action="store_true", help="使用 Mock 模式离线执行")
    p_pipe.add_argument("--model", default=None, help="指定模型名称")
    p_pipe.add_argument("--period", default="社群用户反馈洞察分析周报", help="报告周期标题")
    p_pipe.add_argument("--output", "-o", default=None, help="导出 Markdown 文件路径")

    args = parser.parse_args()
    if args.command == "scan":
        asyncio.run(cmd_scan(args))
    elif args.command == "import":
        asyncio.run(cmd_import(args))
    elif args.command == "analyze-media":
        asyncio.run(cmd_analyze_media(args))
    elif args.command == "segment-episodes":
        asyncio.run(cmd_segment_episodes(args))
    elif args.command == "extract-insights":
        asyncio.run(cmd_extract_insights(args))
    elif args.command == "cluster-insights":
        asyncio.run(cmd_cluster_insights(args))
    elif args.command in ("push-bitable", "sync-abc"):
        asyncio.run(cmd_push_bitable(args))
    elif args.command == "search":
        asyncio.run(cmd_search(args))
    elif args.command == "ask-assistant":
        asyncio.run(cmd_ask_assistant(args))
    elif args.command == "generate-report":
        asyncio.run(cmd_generate_report(args))
    elif args.command == "pipeline":
        asyncio.run(cmd_pipeline(args))


if __name__ == "__main__":
    main()

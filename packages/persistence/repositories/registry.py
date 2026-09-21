from datetime import datetime
from typing import Any, Optional
from sqlalchemy import asc, case, desc, func, or_, select, String
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models import anonymous_label, generate_id, sha256_text
from packages.persistence.models import (
    AnalysisRun,
    AuditEvent,
    Conversation,
    DomainEventOutbox,
    Episode,
    EpisodeMessage,
    ImportBatch,
    Insight,
    InsightClaim,
    MediaAsset,
    MediaEnrichment,
    Message,
    MessageMediaLink,
    Participant,
    ParticipantAlias,
    SourceFile,
    SourceRoot,
    Topic,
    TopicInsightLink,
    Workspace,
)


class RepositoryRegistry:
    def __init__(self, session: AsyncSession):
        self.session = session

    # Workspace
    async def get_or_create_default_workspace(self, name: str = "Default Workspace") -> Workspace:
        stmt = select(Workspace).limit(1)
        result = await self.session.execute(stmt)
        ws = result.scalar_one_or_none()
        if not ws:
            ws = Workspace(
                id=generate_id(),
                name=name,
                timezone="Asia/Shanghai",
                salt_hex="0123456789abcdef0123456789abcdef",
                settings_json={},
            )
            self.session.add(ws)
            await self.session.flush()
        return ws

    # SourceRoot
    async def get_or_create_source_root(
        self, workspace_id: str, display_name: str, root_path: str, source_type: str = "wechat_archive"
    ) -> SourceRoot:
        stmt = select(SourceRoot).where(
            SourceRoot.workspace_id == workspace_id, SourceRoot.display_name == display_name
        )
        res = await self.session.execute(stmt)
        root = res.scalar_one_or_none()
        if not root:
            root = SourceRoot(
                id=generate_id(),
                workspace_id=workspace_id,
                source_type=source_type,
                display_name=display_name,
                root_path=root_path,
                read_only=True,
                enabled=True,
            )
            self.session.add(root)
            await self.session.flush()
        return root

    # SourceFile
    async def upsert_source_file(
        self,
        workspace_id: str,
        source_root_id: str,
        relative_path: str,
        file_kind: str,
        size_bytes: int,
        sha256: str,
    ) -> SourceFile:
        stmt = select(SourceFile).where(
            SourceFile.source_root_id == source_root_id,
            SourceFile.relative_path == relative_path,
            SourceFile.sha256 == sha256,
        )
        res = await self.session.execute(stmt)
        sf = res.scalar_one_or_none()
        if not sf:
            sf = SourceFile(
                id=generate_id(),
                workspace_id=workspace_id,
                source_root_id=source_root_id,
                relative_path=relative_path,
                file_kind=file_kind,
                size_bytes=size_bytes,
                sha256=sha256,
            )
            self.session.add(sf)
            await self.session.flush()
        return sf

    # Conversation
    async def upsert_conversation(
        self,
        workspace_id: str,
        source_type: str,
        source_conversation_id: str,
        display_name: str,
    ) -> Conversation:
        stmt = select(Conversation).where(
            Conversation.workspace_id == workspace_id,
            Conversation.source_type == source_type,
            Conversation.source_conversation_id == source_conversation_id,
        )
        res = await self.session.execute(stmt)
        conv = res.scalar_one_or_none()
        if not conv:
            conv = Conversation(
                id=generate_id(),
                workspace_id=workspace_id,
                source_type=source_type,
                source_conversation_id=source_conversation_id,
                display_name=display_name,
            )
            self.session.add(conv)
            await self.session.flush()
        else:
            if conv.display_name != display_name:
                conv.display_name = display_name
                await self.session.flush()
        return conv

    # Participant
    async def get_or_create_participant(
        self,
        workspace_id: str,
        workspace_salt: bytes,
        display_name: str,
        source_participant_id: str | None = None,
        role_hint: str = "user",
    ) -> Participant:
        stable_identity = source_participant_id or f"nickname_{display_name}"
        anon_key = sha256_text(f"{workspace_id}:{stable_identity}")[:32]

        stmt = select(Participant).where(
            Participant.workspace_id == workspace_id,
            Participant.stable_anonymous_key == anon_key,
        )
        res = await self.session.execute(stmt)
        p = res.scalar_one_or_none()
        if not p:
            raw_label = (display_name or "").strip()
            label = raw_label or anonymous_label(workspace_salt, stable_identity)
            is_internal = role_hint in ["support", "developer", "product_manager", "bot"]
            p = Participant(
                id=generate_id(),
                workspace_id=workspace_id,
                stable_anonymous_key=anon_key,
                display_label=label,
                role=role_hint,
                is_internal=is_internal,
                metadata_json={"raw_nickname": raw_label},
            )
            self.session.add(p)
            await self.session.flush()

            alias = ParticipantAlias(
                id=generate_id(),
                participant_id=p.id,
                source_type="wechat_archive",
                source_participant_id=source_participant_id,
                display_name_ciphertext=display_name,
                display_name_hash=sha256_text(display_name),
            )
            self.session.add(alias)
            await self.session.flush()
        else:
            raw_label = (display_name or "").strip()
            if raw_label and (p.display_label.startswith("用户 U-") or p.display_label.startswith("用户U-")):
                p.display_label = raw_label
                await self.session.flush()
        return p

    # MediaAsset
    async def upsert_media_asset(
        self,
        workspace_id: str,
        source_file_id: str,
        kind: str,
        mime_type: str,
        sha256: str,
        size_bytes: int,
    ) -> MediaAsset:
        stmt = select(MediaAsset).where(
            MediaAsset.workspace_id == workspace_id,
            MediaAsset.sha256 == sha256,
        )
        res = await self.session.execute(stmt)
        media = res.scalar_one_or_none()
        if not media:
            media = MediaAsset(
                id=generate_id(),
                workspace_id=workspace_id,
                source_file_id=source_file_id,
                kind=kind,
                mime_type=mime_type,
                sha256=sha256,
                size_bytes=size_bytes,
            )
            self.session.add(media)
            await self.session.flush()
        return media

    # Message
    async def upsert_message(
        self,
        workspace_id: str,
        conversation_id: str,
        participant_id: str | None,
        source_file_id: str,
        source_message_id: str,
        sequence: int,
        sent_at: datetime,
        raw_text: str,
        source_record_hash: str,
        source_line_start: int | None = None,
        source_line_end: int | None = None,
        quote_unresolved_text: str | None = None,
        mentions_json: list | None = None,
        raw_payload_json: dict | None = None,
    ) -> Message:
        stmt = select(Message).where(
            Message.workspace_id == workspace_id,
            Message.source_record_hash == source_record_hash,
        )
        res = await self.session.execute(stmt)
        msg = res.scalar_one_or_none()
        if not msg:
            msg = Message(
                id=generate_id(),
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                participant_id=participant_id,
                source_file_id=source_file_id,
                source_message_id=source_message_id,
                source_sequence=sequence,
                source_line_start=source_line_start,
                source_line_end=source_line_end,
                sent_at=sent_at,
                source_timezone="Asia/Shanghai",
                raw_text=raw_text,
                normalized_text=raw_text.strip(),
                searchable_text=raw_text,
                quote_unresolved_text=quote_unresolved_text,
                mentions_json=mentions_json or [],
                raw_payload_json=raw_payload_json or {},
                source_record_hash=source_record_hash,
            )
            self.session.add(msg)
            await self.session.flush()
        return msg

    # MessageMediaLink
    async def upsert_media_link(
        self,
        message_id: str,
        media_id: str,
        media_order: int,
        method: str,
        confidence: float,
        state: str,
        evidence_json: dict | None = None,
    ) -> MessageMediaLink:
        stmt = select(MessageMediaLink).where(
            MessageMediaLink.message_id == message_id,
            MessageMediaLink.media_id == media_id,
            MessageMediaLink.revision == 1,
        )
        res = await self.session.execute(stmt)
        link = res.scalar_one_or_none()
        if not link:
            link = MessageMediaLink(
                id=generate_id(),
                message_id=message_id,
                media_id=media_id,
                media_order=media_order,
                method=method,
                confidence=confidence,
                state=state,
                evidence_json=evidence_json or {},
                revision=1,
            )
            self.session.add(link)
            await self.session.flush()
        return link

    # MediaEnrichment
    async def upsert_media_enrichment(
        self,
        workspace_id: str,
        media_id: str,
        analysis_run_id: str,
        summary: str,
        searchable_text: str,
        ocr_json: dict | list | None = None,
        asr_json: dict | list | None = None,
        visual_json: dict | list | None = None,
        timeline_json: list | None = None,
        uncertainty_json: list | None = None,
        state: str = "available",
    ) -> MediaEnrichment:
        stmt = (
            select(MediaEnrichment)
            .where(MediaEnrichment.media_id == media_id)
            .order_by(desc(MediaEnrichment.revision))
            .limit(1)
        )
        res = await self.session.execute(stmt)
        latest = res.scalar_one_or_none()

        new_rev = (latest.revision + 1) if latest else 1

        if latest and latest.superseded_at is None:
            latest.superseded_at = datetime.now()
            latest.state = "superseded"

        enrichment = MediaEnrichment(
            id=generate_id(),
            workspace_id=workspace_id,
            media_id=media_id,
            analysis_run_id=analysis_run_id,
            revision=new_rev,
            state=state,
            summary=summary,
            searchable_text=searchable_text,
            ocr_json=ocr_json,
            asr_json=asr_json,
            visual_json=visual_json,
            timeline_json=timeline_json,
            uncertainty_json=uncertainty_json or [],
            human_override_json={},
        )
        self.session.add(enrichment)
        await self.session.flush()
        return enrichment

    async def get_active_media_enrichment(self, media_id: str) -> MediaEnrichment | None:
        stmt = (
            select(MediaEnrichment)
            .where(MediaEnrichment.media_id == media_id, MediaEnrichment.superseded_at.is_(None))
            .order_by(desc(MediaEnrichment.revision))
            .limit(1)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    # Episode
    async def create_episode(
        self,
        workspace_id: str,
        conversation_id: str,
        title: str,
        summary: str,
        category_hint: str,
        started_at: datetime,
        ended_at: datetime,
        message_ids: list[str],
        participants: list[str],
        media_ids: list[str],
    ) -> Episode:
        ep = Episode(
            id=generate_id(),
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            title=title,
            summary=summary,
            category_hint=category_hint,
            started_at=started_at,
            ended_at=ended_at,
            message_count=len(message_ids),
            participants_json=participants,
            media_json=media_ids,
            state="active",
        )
        self.session.add(ep)
        await self.session.flush()

        for idx, mid in enumerate(message_ids, start=1):
            ep_msg = EpisodeMessage(
                id=generate_id(),
                episode_id=ep.id,
                message_id=mid,
                sequence=idx,
            )
            self.session.add(ep_msg)

        await self.session.flush()
        return ep

    async def get_episodes_by_conversation(self, conversation_id: str) -> list[Episode]:
        stmt = select(Episode).where(Episode.conversation_id == conversation_id).order_by(Episode.started_at.asc())
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_episode_by_id(self, episode_id: str) -> Episode | None:
        stmt = select(Episode).where(Episode.id == episode_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_messages_for_episode(self, episode_id: str) -> list[Message]:
        stmt = (
            select(Message)
            .join(EpisodeMessage, EpisodeMessage.message_id == Message.id)
            .where(EpisodeMessage.episode_id == episode_id)
            .order_by(EpisodeMessage.sequence.asc())
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    # Insight & Claims
    async def create_insight(
        self,
        workspace_id: str,
        episode_id: str,
        insight_type: str,
        module: str,
        summary: str,
        description: str,
        status_in_chat: str = "unresolved",
        support_known_status: bool = False,
        sub_module: str | None = None,
        severity: str = "minor",
        priority: str = "P2",
        factual_score: float = 1.0,
        confidence: float = 0.9,
        tags: list[str] | None = None,
        analysis_run_id: str | None = None,
        device_model: str | None = None,
    ) -> Insight:
        ins = Insight(
            id=generate_id(),
            workspace_id=workspace_id,
            episode_id=episode_id,
            source_type="wechat_archive",
            insight_type=insight_type,
            module=module,
            sub_module=sub_module,
            severity=severity,
            priority=priority,
            device_model=device_model,
            summary=summary,
            description=description,
            status_in_chat=status_in_chat,
            support_known_status=support_known_status,
            factual_score=factual_score,
            confidence=confidence,
            tags_json=tags or [],
            state="draft",
            analysis_run_id=analysis_run_id,
        )
        self.session.add(ins)
        await self.session.flush()
        return ins

    async def add_insight_claim(
        self,
        insight_id: str,
        claim_key: str,
        claim_text: str,
        fact_category: str,
        evidence_uris: list[str],
        confidence: float = 1.0,
        verification_state: str = "supported",
    ) -> InsightClaim:
        c = InsightClaim(
            id=generate_id(),
            insight_id=insight_id,
            claim_key=claim_key,
            claim_text=claim_text,
            fact_category=fact_category,
            evidence_uris_json=evidence_uris,
            confidence=confidence,
            verification_state=verification_state,
        )
        self.session.add(c)
        await self.session.flush()
        return c

    async def get_insights(
        self,
        state: str | None = None,
        insight_type: str | None = None,
        severity: str | None = None,
        module: str | None = None,
        device_model: str | None = None,
        search: str | None = None,
        tag_keys: list[str] | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        sort_by: str = "created_at_desc",
        limit: int = 50,
        offset: int = 0,
    ) -> list[Insight]:
        stmt = select(Insight)
        if state:
            stmt = stmt.where(Insight.state == state)
        if insight_type:
            stmt = stmt.where(Insight.insight_type == insight_type)
        if severity:
            stmt = stmt.where(Insight.severity == severity)
        if module:
            stmt = stmt.where(Insight.module == module)
        if device_model and device_model.strip():
            dm_clean = device_model.strip()
            if dm_clean.lower() in ("none", "general", "null", "未指定", "通用"):
                stmt = stmt.where(or_(Insight.device_model.is_(None), Insight.device_model == ""))
            else:
                stmt = stmt.where(Insight.device_model.ilike(f"%{dm_clean}%"))
        if search and search.strip():
            kw = f"%{search.strip()}%"
            stmt = stmt.where(or_(Insight.summary.ilike(kw), Insight.description.ilike(kw), Insight.module.ilike(kw)))
        if start_date:
            stmt = stmt.where(Insight.created_at >= start_date)
        if end_date:
            stmt = stmt.where(Insight.created_at <= end_date)

        if tag_keys and len(tag_keys) > 0:
            or_clauses = []
            for tk in tag_keys:
                tk_clean = tk.strip()
                if tk_clean:
                    or_clauses.append(Insight.tags_json.cast(String).ilike(f"%{tk_clean}%"))
                    or_clauses.append(Insight.module.ilike(f"%{tk_clean}%"))
            if or_clauses:
                stmt = stmt.where(or_(*or_clauses))

        # Dynamic sorting
        if sort_by == "created_at_asc":
            stmt = stmt.order_by(asc(Insight.created_at))
        elif sort_by == "confidence_desc":
            stmt = stmt.order_by(desc(Insight.confidence), desc(Insight.factual_score), desc(Insight.created_at))
        elif sort_by == "severity_desc":
            sev_rank = case(
                (Insight.severity == "blocker", 4),
                (Insight.severity == "major", 3),
                (Insight.severity == "minor", 2),
                (Insight.severity == "trivial", 1),
                else_=0
            )
            stmt = stmt.order_by(desc(sev_rank), desc(Insight.created_at))
        else:  # created_at_desc
            stmt = stmt.order_by(desc(Insight.created_at))

        stmt = stmt.offset(offset).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_insights_for_stats(
        self,
        state: str | None = None,
        insight_type: str | None = None,
        severity: str | None = None,
        module: str | None = None,
        device_model: str | None = None,
        search: str | None = None,
        tag_keys: list[str] | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[Insight]:
        """Fetches all insights matching criteria without pagination limit for accurate aggregate metrics."""
        stmt = select(Insight)
        if state:
            stmt = stmt.where(Insight.state == state)
        if insight_type:
            stmt = stmt.where(Insight.insight_type == insight_type)
        if severity:
            stmt = stmt.where(Insight.severity == severity)
        if module:
            stmt = stmt.where(Insight.module == module)
        if device_model and device_model.strip():
            dm_clean = device_model.strip()
            if dm_clean.lower() in ("none", "general", "null", "未指定", "通用"):
                stmt = stmt.where(or_(Insight.device_model.is_(None), Insight.device_model == ""))
            else:
                stmt = stmt.where(Insight.device_model.ilike(f"%{dm_clean}%"))
        if search and search.strip():
            kw = f"%{search.strip()}%"
            stmt = stmt.where(or_(Insight.summary.ilike(kw), Insight.description.ilike(kw), Insight.module.ilike(kw)))
        if start_date:
            stmt = stmt.where(Insight.created_at >= start_date)
        if end_date:
            stmt = stmt.where(Insight.created_at <= end_date)

        if tag_keys and len(tag_keys) > 0:
            or_clauses = []
            for tk in tag_keys:
                tk_clean = tk.strip()
                if tk_clean:
                    or_clauses.append(Insight.tags_json.cast(String).ilike(f"%{tk_clean}%"))
                    or_clauses.append(Insight.module.ilike(f"%{tk_clean}%"))
            if or_clauses:
                stmt = stmt.where(or_(*or_clauses))

        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_insight_by_id(self, insight_id: str) -> Insight | None:
        stmt = select(Insight).where(Insight.id == insight_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_claims_for_insight(self, insight_id: str) -> list[InsightClaim]:
        stmt = select(InsightClaim).where(InsightClaim.insight_id == insight_id)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def update_insight_review(
        self,
        insight_id: str,
        state: str,
        reviewed_by: str,
        rejection_reason: str | None = None,
        summary_override: str | None = None,
        module_override: str | None = None,
        severity_override: str | None = None,
    ) -> Insight | None:
        ins = await self.get_insight_by_id(insight_id)
        if not ins:
            return None
        ins.state = state
        ins.reviewed_by = reviewed_by
        ins.reviewed_at = datetime.now()
        if rejection_reason:
            ins.rejection_reason = rejection_reason
        if summary_override:
            ins.summary = summary_override
        if module_override:
            ins.module = module_override
        if severity_override:
            ins.severity = severity_override
        await self.session.flush()
        return ins

    # Topics & Clustering (Phase 4)
    async def create_topic(
        self,
        workspace_id: str,
        title: str,
        summary: str,
        module: str,
        sub_module: str | None = None,
        severity: str = "minor",
        tags: list[str] | None = None,
        first_seen_at: datetime | None = None,
    ) -> Topic:
        t_now = first_seen_at or datetime.now()
        topic = Topic(
            id=generate_id(),
            workspace_id=workspace_id,
            title=title,
            summary=summary,
            module=module,
            sub_module=sub_module,
            severity=severity,
            status="open",
            feedback_count=1,
            unique_users_count=1,
            first_seen_at=t_now,
            last_seen_at=t_now,
            tags_json=tags or [],
            abc_sync_state="not_synced",
        )
        self.session.add(topic)
        await self.session.flush()
        return topic

    async def get_topic_by_id(self, topic_id: str) -> Topic | None:
        stmt = select(Topic).where(Topic.id == topic_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_topics(
        self,
        module: str | None = None,
        status: str | None = None,
        abc_sync_state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Topic]:
        stmt = select(Topic)
        if module:
            stmt = stmt.where(Topic.module == module)
        if status:
            stmt = stmt.where(Topic.status == status)
        if abc_sync_state:
            stmt = stmt.where(Topic.abc_sync_state == abc_sync_state)
        stmt = stmt.order_by(desc(Topic.feedback_count), desc(Topic.last_seen_at)).offset(offset).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_active_topics_by_module(self, module: str) -> list[Topic]:
        stmt = select(Topic).where(Topic.module == module, Topic.status.in_(["open", "in_progress"]))
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def link_insight_to_topic(
        self,
        topic_id: str,
        insight_id: str,
        relation_type: str = "instance",
    ) -> TopicInsightLink:
        stmt = select(TopicInsightLink).where(
            TopicInsightLink.topic_id == topic_id,
            TopicInsightLink.insight_id == insight_id,
        )
        res = await self.session.execute(stmt)
        link = res.scalar_one_or_none()
        if not link:
            link = TopicInsightLink(
                id=generate_id(),
                topic_id=topic_id,
                insight_id=insight_id,
                relation_type=relation_type,
            )
            self.session.add(link)
            await self.session.flush()
        return link

    async def get_insights_for_topic(self, topic_id: str) -> list[Insight]:
        stmt = (
            select(Insight)
            .join(TopicInsightLink, TopicInsightLink.insight_id == Insight.id)
            .where(TopicInsightLink.topic_id == topic_id)
            .order_by(desc(Insight.created_at))
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def update_topic_stats(
        self,
        topic_id: str,
        increment_feedback: int = 1,
        increment_users: int = 1,
        new_seen_time: datetime | None = None,
        merged_title: str | None = None,
        merged_summary: str | None = None,
    ) -> Topic | None:
        topic = await self.get_topic_by_id(topic_id)
        if not topic:
            return None
        topic.feedback_count += increment_feedback
        topic.unique_users_count += increment_users
        if new_seen_time and new_seen_time > topic.last_seen_at:
            topic.last_seen_at = new_seen_time
        if merged_title:
            topic.title = merged_title
        if merged_summary:
            topic.summary = merged_summary
        await self.session.flush()
        return topic

    async def merge_topics(
        self,
        source_topic_id: str,
        target_topic_id: str,
    ) -> Topic | None:
        source = await self.get_topic_by_id(source_topic_id)
        target = await self.get_topic_by_id(target_topic_id)
        if not source or not target:
            return None

        # Re-link all insights from source to target
        links_stmt = select(TopicInsightLink).where(TopicInsightLink.topic_id == source_topic_id)
        links = (await self.session.execute(links_stmt)).scalars().all()
        for link in links:
            await self.link_insight_to_topic(target_topic_id, link.insight_id, link.relation_type)
            await self.session.delete(link)

        # Update target stats
        target.feedback_count += source.feedback_count
        target.unique_users_count += source.unique_users_count
        if source.last_seen_at > target.last_seen_at:
            target.last_seen_at = source.last_seen_at
        if source.first_seen_at < target.first_seen_at:
            target.first_seen_at = source.first_seen_at

        source.status = "closed"
        source.summary = f"[已合并至 {target.title} ({target.id})] {source.summary}"
        await self.session.flush()
        return target

    # Domain Events & Outbox (Phase 4)
    async def record_outbox_event(
        self,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> DomainEventOutbox:
        stmt = select(DomainEventOutbox).where(DomainEventOutbox.idempotency_key == idempotency_key)
        res = await self.session.execute(stmt)
        event = res.scalar_one_or_none()
        if not event:
            event = DomainEventOutbox(
                id=generate_id(),
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                idempotency_key=idempotency_key,
                payload_json=payload,
                state="queued",
            )
            self.session.add(event)
            await self.session.flush()
        return event

    # ImportBatch
    async def start_or_resume_batch(
        self,
        workspace_id: str,
        source_root_id: str,
        batch_key: str,
        date_str: str,
        conversation_name: str,
        input_hash: str,
    ) -> ImportBatch:
        stmt = select(ImportBatch).where(
            ImportBatch.workspace_id == workspace_id,
            ImportBatch.batch_key == batch_key,
        )
        res = await self.session.execute(stmt)
        batch = res.scalar_one_or_none()
        if not batch:
            batch = ImportBatch(
                id=generate_id(),
                workspace_id=workspace_id,
                source_root_id=source_root_id,
                batch_key=batch_key,
                date_str=date_str,
                conversation_name=conversation_name,
                input_hash=input_hash,
                state="parsing",
            )
            self.session.add(batch)
            await self.session.flush()
        return batch

    # AuditEvent
    async def record_audit(
        self,
        workspace_id: str,
        action: str,
        object_type: str,
        object_id: str,
        actor_id: str = "system",
        metadata_json: dict | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            id=generate_id(),
            workspace_id=workspace_id,
            actor_type="system" if actor_id == "system" else "user",
            actor_id=actor_id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            metadata_json=metadata_json or {},
        )
        self.session.add(event)
        await self.session.flush()
        return event

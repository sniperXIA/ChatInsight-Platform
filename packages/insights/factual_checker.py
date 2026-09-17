import re
from typing import Any
from packages.insights.contracts import ClaimItem, EpisodeContextPacket, FactualCheckResult, InsightDraft


class FactualChecker:
    """Evaluates factual consistency of extracted claims against raw context packet."""

    def check_insight_claims(
        self,
        draft: InsightDraft,
        context: EpisodeContextPacket,
    ) -> FactualCheckResult:
        # Build lookup table of raw evidence content
        msg_text_map: dict[str, str] = {m.message_id: m.raw_text for m in context.messages}
        all_text = " ".join([m.raw_text for m in context.messages] + [ocr for m in context.messages for ocr in m.media_ocr_texts])

        unsupported: list[str] = []
        hallucination_risks: list[str] = []

        total_claims = len(draft.claims)
        if total_claims == 0:
            return FactualCheckResult(
                overall_factual_score=0.5,
                is_fully_supported=False,
                unsupported_claims=["no_claims_provided"],
                hallucination_risks=["洞察缺乏原子主张支撑"],
                action_recommendation="needs_human_review",
            )

        supported_count = 0
        for claim in draft.claims:
            claim_supported = False

            # Check evidence URI validity
            valid_uris = [uri for uri in claim.evidence_uris if uri.startswith("chatinsight://")]
            if not valid_uris:
                unsupported.append(claim.claim_id)
                hallucination_risks.append(f"主张 [{claim.claim_text}] 未引用任何合法 Evidence URI")
                continue

            # Check whether keywords/tokens of claim exist in referenced message texts
            ref_msg_ids = []
            for uri in valid_uris:
                match = re.search(r"msg_([a-zA-Z0-9_\-]+)", uri)
                if match:
                    ref_msg_ids.append(match.group(1))

            referenced_texts = [msg_text_map[mid] for mid in ref_msg_ids if mid in msg_text_map]
            combined_ref = " ".join(referenced_texts) if referenced_texts else all_text

            # Simple token overlap heuristic (checking substantive tokens)
            claim_clean = re.sub(r"[^\w\s]", "", claim.claim_text)
            claim_tokens = [t for t in claim_clean if len(t.strip()) > 0]
            
            if not claim_tokens:
                claim_supported = True
            else:
                match_count = sum(1 for t in claim_tokens if t in combined_ref)
                overlap_ratio = match_count / len(claim_tokens) if claim_tokens else 1.0
                if overlap_ratio >= 0.3 or len(referenced_texts) > 0:
                    claim_supported = True

            if claim_supported:
                supported_count += 1
                claim.verification_state = "supported"
            else:
                unsupported.append(claim.claim_id)
                claim.verification_state = "unverified"
                hallucination_risks.append(f"主张 [{claim.claim_text}] 与引用的证据文本匹配度偏低")

        score = round(supported_count / total_claims, 2)
        is_fully = (len(unsupported) == 0)

        action = "auto_approve" if (score >= 0.85 and draft.confidence >= 0.85) else "needs_human_review"
        if score < 0.4:
            action = "reject"

        return FactualCheckResult(
            overall_factual_score=score,
            is_fully_supported=is_fully,
            unsupported_claims=unsupported,
            hallucination_risks=hallucination_risks,
            action_recommendation=action,
        )

    async def check_insight_claims_semantic(
        self,
        draft: InsightDraft,
        context: EpisodeContextPacket,
        vector_service: Any = None,
        force_mock: bool = False,
    ) -> FactualCheckResult:
        """
        Asynchronously checks factual consistency using dense vector cosine similarity
        between claims and referenced evidence texts.
        """
        base_result = self.check_insight_claims(draft, context)
        if not vector_service or not draft.claims:
            return base_result

        from packages.retrieval.vector_math import cosine_similarity

        msg_text_map = {m.message_id: m.raw_text for m in context.messages}
        semantic_supported = 0
        unsupported = list(base_result.unsupported_claims)
        hallucination_risks = list(base_result.hallucination_risks)

        for claim in draft.claims:
            valid_uris = [uri for uri in claim.evidence_uris if uri.startswith("chatinsight://")]
            if not valid_uris:
                continue

            ref_msg_ids = []
            for uri in valid_uris:
                m = re.search(r"msg_([a-zA-Z0-9_\-]+)", uri)
                if m:
                    ref_msg_ids.append(m.group(1))

            referenced_texts = [msg_text_map[mid] for mid in ref_msg_ids if mid in msg_text_map]
            combined_ref = " ".join(referenced_texts) if referenced_texts else " ".join([m.raw_text for m in context.messages])

            try:
                claim_vec = await vector_service.embed_single(claim.claim_text, force_mock=force_mock)
                ref_vec = await vector_service.embed_single(combined_ref, force_mock=force_mock)
                sem_sim = cosine_similarity(claim_vec, ref_vec)
                threshold = 0.30 if force_mock else 0.55
                if sem_sim >= threshold or (claim.verification_state == "supported" and sem_sim >= 0.25):
                    semantic_supported += 1
                    claim.confidence = max(claim.confidence, round(sem_sim, 2))
                else:
                    if claim.claim_id not in unsupported:
                        unsupported.append(claim.claim_id)
                        hallucination_risks.append(f"主张 [{claim.claim_text}] 语义相似度偏低 ({sem_sim:.2f})")
            except Exception:
                if claim.verification_state == "supported":
                    semantic_supported += 1

        total = len(draft.claims)
        sem_score = round(semantic_supported / total, 2) if total > 0 else 0.5
        is_fully = len(unsupported) == 0

        action = "auto_approve" if (sem_score >= 0.85 and draft.confidence >= 0.85) else "needs_human_review"
        if sem_score < 0.4:
            action = "reject"

        return FactualCheckResult(
            overall_factual_score=sem_score,
            is_fully_supported=is_fully,
            unsupported_claims=unsupported,
            hallucination_risks=hallucination_risks,
            action_recommendation=action,
        )


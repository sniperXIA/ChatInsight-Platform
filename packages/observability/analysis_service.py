import json
from datetime import datetime
from typing import Any, Callable, Coroutine, Type, TypeVar
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models import canonical_json_hash, generate_id, sha256_text
from packages.persistence.models import AnalysisRun

T = TypeVar("T", bound=BaseModel)


class AnalysisService:
    """Manages model analysis executions with caching, token metrics, and run history."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def execute_cached(
        self,
        workspace_id: str,
        run_type: str,
        target_type: str,
        target_id: str,
        provider: str,
        model: str,
        prompt_key: str,
        input_data: Any,
        config_data: Any,
        response_schema: Type[T],
        call_fn: Callable[[], Coroutine[Any, Any, tuple[T, dict[str, Any]]]],
        schema_version: str = "v2",
        prompt_version: str = "1.0",
        bypass_cache: bool = False,
    ) -> tuple[T, AnalysisRun, bool]:
        """Returns (validated_result, analysis_run_record, was_cache_hit)."""
        input_hash = canonical_json_hash(input_data)
        config_hash = canonical_json_hash({"provider": provider, "model": model, "config": config_data})

        # 1. Check existing run record (matches exact unique constraint columns)
        stmt = select(AnalysisRun).where(
            AnalysisRun.run_type == run_type,
            AnalysisRun.target_type == target_type,
            AnalysisRun.target_id == target_id,
            AnalysisRun.input_hash == input_hash,
            AnalysisRun.config_hash == config_hash,
        )
        res = await self.session.execute(stmt)
        existing_run = res.scalar_one_or_none()

        if not bypass_cache and existing_run and existing_run.state == "succeeded" and existing_run.raw_response:
            try:
                cached_obj = response_schema.model_validate_json(existing_run.raw_response)
                return cached_obj, existing_run, True
            except Exception:
                pass  # Fall through to re-execute if cache parsing fails

        # 2. Prepare run metadata (Do not lock DB during remote network call)
        run_id = existing_run.id if existing_run else generate_id()
        started_at = datetime.now()

        # 3. Call Model Provider (Pure async network I/O outside DB write transaction)
        try:
            result_obj, usage = await call_fn()
            
            # Re-check existing_run in case an identical run was created during call_fn
            if not existing_run:
                res_latest = await self.session.execute(stmt)
                existing_run = res_latest.scalar_one_or_none()

            if existing_run:
                existing_run.provider = provider
                existing_run.model = model
                existing_run.state = "succeeded"
                existing_run.started_at = started_at
                existing_run.completed_at = datetime.now()
                existing_run.input_tokens = usage.get("prompt_tokens") if usage else None
                existing_run.output_tokens = usage.get("completion_tokens") if usage else None
                existing_run.total_tokens = usage.get("total_tokens") if usage else None
                existing_run.raw_response = result_obj.model_dump_json()
                existing_run.error_json = None
                run = existing_run
            else:
                run = AnalysisRun(
                    id=run_id,
                    workspace_id=workspace_id,
                    run_type=run_type,
                    target_type=target_type,
                    target_id=target_id,
                    provider=provider,
                    model=model,
                    prompt_key=prompt_key,
                    prompt_version=prompt_version,
                    schema_version=schema_version,
                    input_hash=input_hash,
                    config_hash=config_hash,
                    state="succeeded",
                    started_at=started_at,
                    completed_at=datetime.now(),
                    input_tokens=usage.get("prompt_tokens") if usage else None,
                    output_tokens=usage.get("completion_tokens") if usage else None,
                    total_tokens=usage.get("total_tokens") if usage else None,
                    raw_response=result_obj.model_dump_json(),
                )
                self.session.add(run)
            await self.session.flush()
            return result_obj, run, False
        except Exception as exc:
            if existing_run:
                existing_run.state = "failed"
                existing_run.completed_at = datetime.now()
                existing_run.error_json = {"error": str(exc), "type": type(exc).__name__}
                run = existing_run
            else:
                run = AnalysisRun(
                    id=run_id,
                    workspace_id=workspace_id,
                    run_type=run_type,
                    target_type=target_type,
                    target_id=target_id,
                    provider=provider,
                    model=model,
                    prompt_key=prompt_key,
                    prompt_version=prompt_version,
                    schema_version=schema_version,
                    input_hash=input_hash,
                    config_hash=config_hash,
                    state="failed",
                    started_at=started_at,
                    completed_at=datetime.now(),
                    error_json={"error": str(exc), "type": type(exc).__name__},
                )
                self.session.add(run)
            try:
                await self.session.flush()
            except Exception:
                pass
            raise

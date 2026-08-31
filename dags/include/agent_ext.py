"""The graph agent's query budget, and an operator that scores running out of it
as a miss instead of failing the whole mapped task group.
"""

from __future__ import annotations

from typing import Any

from airflow.providers.common.ai.operators.agent import AgentOperator
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.usage import UsageLimits

# Shared by the benchmark and the live demo so both run under the same budget.
GRAPH_QUERY_BUDGET = 12

# Billed knob: each round of tool calls costs one model request, so N queries
# need N+1 requests (the extra one writes the final answer).
GRAPH_REQUEST_LIMIT = GRAPH_QUERY_BUDGET + 1

# NOT a quota knob (Gremlin runs locally, free) -- a runaway backstop, kept
# above GRAPH_REQUEST_LIMIT since one round may issue several queries at once.
GRAPH_TOOL_CALL_LIMIT = 2 * GRAPH_QUERY_BUDGET

GRAPH_USAGE_LIMITS = UsageLimits(
    request_limit=GRAPH_REQUEST_LIMIT,
    tool_calls_limit=GRAPH_TOOL_CALL_LIMIT,
)


class BudgetedAgentOperator(AgentOperator):
    """``AgentOperator`` that scores an agent's failure as a miss instead of failing the task.

    ``UsageLimitExceeded`` scores immediately (deterministic, retrying won't help).
    ``UnexpectedModelBehavior`` scores only on the final retry attempt.
    Everything else (network errors, missing keys, etc.) still propagates.
    """

    def execute(self, context) -> Any:
        try:
            return super().execute(context)
        except UsageLimitExceeded as exc:
            return self._score_as_miss(context, "budget exhausted", exc)
        except UnexpectedModelBehavior as exc:
            if not self._is_final_attempt(context):
                raise
            return self._score_as_miss(context, "agent gave up", exc)

    def _is_final_attempt(self, context) -> bool:
        """True when Airflow has no retries left."""
        try_number = getattr(context.get("task_instance"), "try_number", None)
        if try_number is None:
            return True
        return try_number > (self.retries or 0)

    def _score_as_miss(self, context, reason: str, exc: Exception) -> Any:
        self.log.warning(
            "Graph agent could not answer this question (%s: %s). Returning an "
            "empty answer, which scores as the miss it is rather than failing "
            "the run.",
            reason,
            exc,
        )
        self._discard_durable_cache()
        return self._empty_output(f"{reason}: {exc}")

    def _empty_output(self, note: str) -> Any:
        """An all-defaults ``output_type`` instance, dumped to dict/instance to match
        whatever shape the operator would otherwise return (``as_answer`` handles both).
        The failure reason is stashed in ``citations`` for visibility; scoring ignores it.
        """
        from pydantic import BaseModel

        output_type = self.output_type
        if not (isinstance(output_type, type) and issubclass(output_type, BaseModel)):
            return None
        empty = output_type()
        if "citations" in type(empty).model_fields:
            empty.citations = [note]
        if self._serialize_model_output:
            return empty.model_dump()
        return empty

    def _discard_durable_cache(self) -> None:
        """Drop the replay cache now that this failure has been turned into a scored miss
        (the operator only cleans it up after a genuine success). Best-effort.
        """
        storage = getattr(self, "_durable_storage", None)
        if storage is None:
            return
        try:
            storage.cleanup()
        except Exception:
            self.log.debug(
                "Durable cache cleanup after a budget overrun failed", exc_info=True
            )

"""Thin wrapper around the Anthropic Messages API with tool use.

Every request is written to the audit log: purpose, input summary, tool calls
with their results, output text, stop reason and token counts. The model name
comes from ``ANTHROPIC_MODEL``; without it the agent is unavailable and callers
fall back to deterministic templates.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import anthropic
from sqlalchemy.orm import Session

from ducan.audit.log import record
from ducan.config import EnvSettings

MAX_TOKENS = 4096
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMUnavailable(RuntimeError):
    """No model configured, or the API call failed."""


@dataclass
class Tool:
    """A tool the model may call. ``handler`` is None for the final (submit) tool."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any] | None = None

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "strict": True,
        }


@dataclass
class AgentRun:
    """Outcome of :meth:`Agent.run`."""

    submitted: dict[str, Any] | None
    submitted_by: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None


def _summarize(value: Any, limit: int = 500) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


class Agent:
    """Runs one bounded tool-use conversation."""

    def __init__(self, env: EnvSettings, client: Any | None = None) -> None:
        if not env.anthropic_model:
            raise LLMUnavailable("ANTHROPIC_MODEL nije postavljen.")
        self.model = env.anthropic_model
        self.effort = env.anthropic_effort or None
        self.fallback = env.anthropic_refusal_fallback or None
        self.client = client or anthropic.Anthropic(api_key=env.anthropic_api_key or None)

    def _create(self, **kwargs: Any) -> Any:
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        if self.fallback:
            return self.client.beta.messages.create(
                betas=[FALLBACK_BETA], fallbacks=self.fallback, **kwargs
            )
        return self.client.messages.create(**kwargs)

    def run(
        self,
        session: Session,
        *,
        purpose: str,
        system: str,
        user: str,
        tools: list[Tool],
        submit_tool: str | tuple[str, ...],
        max_turns: int = 6,
        action_id: int | None = None,
    ) -> AgentRun:
        """Loop until the model calls a submit tool (or gives up). Raises LLMUnavailable."""
        finals = (submit_tool,) if isinstance(submit_tool, str) else submit_tool
        by_name = {t.name: t for t in tools}
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        outcome = AgentRun(submitted=None)
        for _turn in range(max_turns):
            try:
                response = self._create(
                    model=self.model,
                    max_tokens=MAX_TOKENS,
                    system=system,
                    tools=[t.definition() for t in tools],
                    messages=messages,
                )
            except anthropic.APIError as exc:
                record(
                    session,
                    actor="agent",
                    event="llm.error",
                    summary=purpose,
                    details={"error": type(exc).__name__, "message": str(exc)[:500]},
                    action_id=action_id,
                )
                raise LLMUnavailable(str(exc)) from exc

            outcome.stop_reason = response.stop_reason
            calls: list[dict[str, Any]] = []
            results: list[dict[str, Any]] = []
            text = "".join(b.text for b in response.content if b.type == "text")
            for block in response.content:
                if block.type != "tool_use":
                    continue
                tool = by_name.get(block.name)
                if block.name in finals:
                    outcome.submitted = dict(block.input)
                    outcome.submitted_by = block.name
                    calls.append({"name": block.name, "input": block.input})
                    continue
                try:
                    if tool is None or tool.handler is None:
                        raise ValueError(f"unknown tool {block.name}")
                    result = tool.handler(dict(block.input))
                    content, is_error = json.dumps(result, ensure_ascii=False, default=str), False
                except Exception as exc:  # report tool errors back to the model
                    content, is_error = f"Error: {exc}", True
                calls.append(
                    {
                        "name": block.name,
                        "input": block.input,
                        "result": _summarize(content),
                        "is_error": is_error,
                    }
                )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": content,
                        "is_error": is_error,
                    }
                )
            outcome.tool_calls.extend(calls)

            usage = response.usage
            record(
                session,
                actor="agent",
                event="llm.call",
                summary=purpose,
                action_id=action_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                details={
                    "model": getattr(response, "model", self.model),
                    "input_summary": {
                        "system_chars": len(system),
                        "user": _summarize(user),
                        "turn": _turn + 1,
                    },
                    "tool_calls": calls,
                    "output_text": _summarize(text, 2000),
                    "stop_reason": response.stop_reason,
                    "usage": {
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
                    },
                },
            )
            if outcome.submitted is not None or response.stop_reason in ("refusal", "max_tokens"):
                return outcome
            messages.append({"role": "assistant", "content": response.content})
            if results:
                messages.append({"role": "user", "content": results})
            else:
                messages.append(
                    {"role": "user", "content": f"Call the {' or '.join(finals)} tool to finish."}
                )
        return outcome

"""Minimal agent harness: structured LLM calls with validation + repair.

Every agent call goes through `structured_call`: the model must return JSON
matching a pydantic schema; on a validation failure the error is fed back once
for repair, then we fail loudly. This is the project's small version of the
harness pattern (structured-output enforcement, bounded retries) — one place
owns it instead of each agent reimplementing it.

Model tiers are env-driven so cost routing is a config choice, not a code
change: CHEAP_MODEL for planning/synthesis by default; set SYNTHESIS_MODEL
to a stronger model when answer quality warrants it.
"""

import os
from typing import Protocol, TypeVar

import structlog
from pydantic import BaseModel, ValidationError

log = structlog.get_logger(__name__)

CHEAP_MODEL = os.environ.get("CHEAP_MODEL", "gpt-4o-mini")
SYNTHESIS_MODEL = os.environ.get("SYNTHESIS_MODEL", CHEAP_MODEL)

T = TypeVar("T", bound=BaseModel)

MAX_REPAIR_ATTEMPTS = 1


class Chat(Protocol):
    """One JSON-mode chat completion. Implementations: OpenAIChat, test fakes."""

    def __call__(self, system: str, user: str, model: str) -> str: ...


class OpenAIChat:
    def __init__(self) -> None:
        from openai import OpenAI

        self._client = OpenAI()

    def __call__(self, system: str, user: str, model: str) -> str:
        response = self._client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""


class StructuredCallFailed(Exception):
    pass


def structured_call(chat: Chat, system: str, user: str, schema: type[T], model: str) -> T:
    prompt = user
    for attempt in range(MAX_REPAIR_ATTEMPTS + 1):
        raw = chat(system=system, user=prompt, model=model)
        try:
            return schema.model_validate_json(raw)
        except ValidationError as exc:
            log.warning("structured_output_invalid", schema=schema.__name__, attempt=attempt, error=str(exc))
            # Feed the validation error back — a targeted repair beats a blind retry.
            prompt = (
                f"{user}\n\nYour previous response failed validation with:\n{exc}\n"
                f"Respond again with ONLY valid JSON matching the required schema."
            )
    raise StructuredCallFailed(f"{schema.__name__} still invalid after {MAX_REPAIR_ATTEMPTS} repair attempt(s)")

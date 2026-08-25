"""
Thin wrapper around the OpenAI SDK used by every AI analyzer.

Centralizes: client construction, forcing JSON-only structured output,
and robust parsing/error handling so individual analyzers stay small.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI, OpenAIError

from app.config import get_settings

logger = logging.getLogger(__name__)


class OpenAIAnalysisClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self._client: AsyncOpenAI | None = (
            AsyncOpenAI(api_key=settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else None
        )

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """Send a chat completion request constrained to JSON output and
        return the parsed object. Raises RuntimeError with a clear message
        if the API key is missing or the call fails."""

        if not self.is_configured:
            raise RuntimeError(
                "OPENAI_API_KEY is not configured. Set it in the environment/.env file."
            )

        try:
            response = await self._client.chat.completions.create(
                model=self._settings.OPENAI_MODEL,
                temperature=self._settings.OPENAI_TEMPERATURE,
                max_tokens=self._settings.OPENAI_MAX_TOKENS,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except OpenAIError as exc:
            logger.error("OpenAI request failed: %s", exc)
            raise RuntimeError(f"OpenAI request failed: {exc}") from exc

        content = response.choices[0].message.content or "{}"

        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse OpenAI JSON response: %s | content=%s", exc, content)
            raise RuntimeError("OpenAI returned a response that was not valid JSON.") from exc

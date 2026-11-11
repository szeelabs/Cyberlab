"""Claude planner, through the official Anthropic SDK (``pip install 'cyberlab[ai]'``)."""

from __future__ import annotations

import json
from typing import Any

from cyberlab.architect.core import ArchitectError, ArchitectRefused, ArchitectUnavailable


class AnthropicModel:
    name = "anthropic"

    def __init__(
        self, *, api_key: str, model: str, timeout: float = 120, client: Any = None
    ) -> None:
        if client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - depends on the install
                raise ArchitectUnavailable(
                    "the Anthropic SDK is not installed; run pip install 'cyberlab[ai]'"
                ) from exc
            client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)
        self._client = client
        self.model = model

    def draft(self, *, system: str, prompt: str, schema: dict) -> dict:
        import anthropic

        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={
                    "effort": "medium",
                    "format": {"type": "json_schema", "schema": schema},
                },
                # If a safety classifier declines (security topics can trip one), the API
                # retries on a fallback model inside the same call.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as exc:
            raise ArchitectUnavailable("the AI service rejected CYBERLAB_AI_API_KEY") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ArchitectUnavailable("the AI key is not allowed to use this model") from exc
        except anthropic.RateLimitError as exc:
            raise ArchitectUnavailable("the AI service is busy; try again in a minute") from exc
        except anthropic.APIStatusError as exc:
            raise ArchitectUnavailable(
                f"the AI service returned an error ({exc.status_code})"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ArchitectUnavailable("could not reach the AI service") from exc

        if response.stop_reason == "refusal":
            raise ArchitectRefused(
                "the AI declined this request. CyberLab builds practice labs for authorized "
                "testing only; try describing the lab you want to practise in."
            )
        if response.stop_reason == "max_tokens":
            raise ArchitectError("the AI's answer was cut off; try a shorter description")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise ArchitectError("the AI returned no draft")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ArchitectError("the AI returned a draft that is not valid JSON") from exc
        if not isinstance(data, dict):
            raise ArchitectError("the AI returned a draft in the wrong shape")
        return data

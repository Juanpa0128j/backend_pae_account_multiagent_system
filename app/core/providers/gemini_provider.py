"""Gemini provider — fallback LLM when OpenAI quota is exhausted."""

from __future__ import annotations

import logging
from typing import Any, Iterator

from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class GeminiProvider:
    """Wraps ChatGoogleGenerativeAI for structured-output extraction."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.gemini_api_key
        self._model_name = settings.gemini_model
        self._models: dict[type[BaseModel], Any] = {}

        if not self._api_key:
            raise ValueError("GEMINI_API_KEY not set")

        self._base = ChatGoogleGenerativeAI(
            model=self._model_name,
            google_api_key=self._api_key,
            temperature=0.0,
            max_output_tokens=8192,
        )
        logger.info("GeminiProvider initialised (%s)", self._model_name)

    def _get_model(self, schema_cls: type[BaseModel]) -> Any:
        if schema_cls not in self._models:
            self._models[schema_cls] = self._base.with_structured_output(schema_cls)
        return self._models[schema_cls]

    def invoke(self, schema_cls: type[BaseModel], prompt: str) -> BaseModel:
        return self._get_model(schema_cls).invoke([HumanMessage(content=prompt)])

    def stream(self, prompt: str) -> Iterator[str]:
        """Stream raw text tokens from the base model (no structured output)."""
        for chunk in self._base.stream([HumanMessage(content=prompt)]):
            if chunk.content:
                yield chunk.content

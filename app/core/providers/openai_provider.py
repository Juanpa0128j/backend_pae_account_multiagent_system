"""OpenAI provider — primary LLM for structured extraction."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class OpenAIProvider:
    """Wraps ChatOpenAI for structured-output extraction."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.openai_api_key
        self._model_name = settings.openai_model
        self._models: dict[type[BaseModel], Any] = {}

        if not self._api_key:
            raise ValueError("OPENAI_API_KEY not set")

        self._base = ChatOpenAI(
            model=self._model_name,
            api_key=self._api_key,
            temperature=0,
        )
        logger.info("OpenAIProvider initialised (%s)", self._model_name)

    def _get_model(self, schema_cls: type[BaseModel]) -> Any:
        if schema_cls not in self._models:
            self._models[schema_cls] = self._base.with_structured_output(
                schema_cls, method="function_calling"
            )
        return self._models[schema_cls]

    def invoke(self, schema_cls: type[BaseModel], prompt: str) -> BaseModel:
        return self._get_model(schema_cls).invoke([HumanMessage(content=prompt)])

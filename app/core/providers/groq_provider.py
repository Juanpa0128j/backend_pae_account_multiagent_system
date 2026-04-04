"""Groq provider — second fallback LLM."""
from __future__ import annotations

import copy
import logging
from typing import Any, Iterator

from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_groq import ChatGroq
from pydantic import BaseModel

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _clean_schema_patterns(schema: dict) -> dict:
    """Recursively remove lookahead regex patterns unsupported by Groq."""
    schema = copy.deepcopy(schema)

    def _walk(obj):
        if isinstance(obj, dict):
            if "pattern" in obj and "(?!" in obj["pattern"]:
                del obj["pattern"]
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(schema)
    return schema


class GroqProvider:
    """Wraps ChatGroq for structured-output extraction."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.groq_api_key
        self._model_name = settings.groq_model
        self._models: dict[type[BaseModel], Any] = {}

        if not self._api_key:
            raise ValueError("GROQ_API_KEY not set")

        self._base = ChatGroq(
            model=self._model_name,
            api_key=self._api_key,
            temperature=0,
        )
        logger.info("GroqProvider initialised (%s)", self._model_name)

    def _get_model(self, schema_cls: type[BaseModel]) -> Any:
        if schema_cls not in self._models:
            cleaned = _clean_schema_patterns(schema_cls.model_json_schema())
            bound = self._base.bind(
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_cls.__name__,
                        "schema": cleaned,
                        "strict": False,
                    },
                }
            )
            self._models[schema_cls] = bound | JsonOutputParser() | schema_cls.model_validate
        return self._models[schema_cls]

    def invoke(self, schema_cls: type[BaseModel], prompt: str) -> BaseModel:
        return self._get_model(schema_cls).invoke([HumanMessage(content=prompt)])

    def stream(self, prompt: str) -> Iterator[str]:
        """Stream raw text tokens from the base model (no structured output)."""
        for chunk in self._base.stream([HumanMessage(content=prompt)]):
            if chunk.content:
                yield chunk.content

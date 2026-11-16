"""Unified LLM provider abstraction.

Supports OpenAI, Anthropic, and any OpenAI-compatible open-source endpoint
(Ollama, vLLM, Groq). Provides:
- Tenacity retries with exponential backoff
- Per-call tracing (tokens, latency, cost estimate)
- On-disk response caching for reproducible evals
- Provider & model configuration via environment variables
"""

from __future__ import annotations

import enum
import os
import time
import uuid
from typing import Any

from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from planpilot.llm.cache import DiskCache
from planpilot.llm.cost import estimate_cost


class Provider(enum.StrEnum):
    """Supported LLM providers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"


class LLMTrace(BaseModel):
    """Detailed trace of an individual LLM API invocation."""

    call_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    provider: Provider
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_seconds: float = 0.0
    cost_usd: float = 0.0
    cached: bool = False
    timestamp: float = Field(default_factory=time.time)


class ToolCall(BaseModel):
    """Standardized representation of a tool/function call."""

    id: str
    name: str
    arguments: dict[str, Any]


class LLMResponse(BaseModel):
    """Unified response returned by the LLM client."""

    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    trace: LLMTrace


class ToolDefinition(BaseModel):
    """Definition of a tool available to the model."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema schema


class LLMClient:
    """Unified client for invoking LLMs across providers with caching and tracing."""

    def __init__(
        self,
        provider: Provider | str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.0,
        enable_cache: bool = True,
        cache_dir: str = ".cache/llm",
    ) -> None:
        raw_provider: str = (
            provider.value
            if isinstance(provider, Provider)
            else (provider or os.getenv("DEFAULT_LLM_PROVIDER") or Provider.OPENAI.value)
        )
        self.provider: Provider = Provider(raw_provider)

        default_model = (
            "gpt-4o"
            if self.provider == Provider.OPENAI
            else ("claude-3-5-sonnet-20241022" if self.provider == Provider.ANTHROPIC else "llama3")
        )
        resolved_model: str = model or os.getenv("DEFAULT_LLM_MODEL") or default_model
        self.model: str = resolved_model
        self.temperature = temperature
        self.enable_cache = enable_cache
        self.cache = DiskCache(cache_dir=cache_dir) if enable_cache else None

        # Credentials & base URLs
        if self.provider == Provider.OPENAI:
            self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
            self.base_url = base_url
        elif self.provider == Provider.ANTHROPIC:
            self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
            self.base_url = base_url
        elif self.provider == Provider.OPENAI_COMPATIBLE:
            self.api_key = api_key or os.getenv("OPENAI_COMPATIBLE_API_KEY", "dummy-key")
            self.base_url = base_url or os.getenv(
                "OPENAI_COMPATIBLE_BASE_URL", "http://localhost:11434/v1"
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        reraise=True,
    )
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        """Execute a completion with caching, tracing, and automatic retries."""
        cache_payload = {
            "provider": self.provider.value,
            "messages": messages,
            "tools": [t.model_dump() for t in tools] if tools else None,
            "temperature": self.temperature,
        }

        # Check cache
        if self.enable_cache and self.cache:
            cached = self.cache.get(self.model, cache_payload)
            if cached is not None:
                p_tokens = cached.get("prompt_tokens", 0)
                c_tokens = cached.get("completion_tokens", 0)
                trace = LLMTrace(
                    provider=self.provider,
                    model=self.model,
                    prompt_tokens=p_tokens,
                    completion_tokens=c_tokens,
                    total_tokens=p_tokens + c_tokens,
                    latency_seconds=0.0,
                    cost_usd=0.0,
                    cached=True,
                )
                tool_calls = [ToolCall(**tc) for tc in cached.get("tool_calls", [])]
                return LLMResponse(
                    content=cached.get("content", ""),
                    tool_calls=tool_calls,
                    trace=trace,
                )

        t0 = time.time()

        if self.provider in (Provider.OPENAI, Provider.OPENAI_COMPATIBLE):
            resp = self._complete_openai(messages, tools)
        elif self.provider == Provider.ANTHROPIC:
            resp = self._complete_anthropic(messages, tools)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

        latency = round(time.time() - t0, 3)
        cost = estimate_cost(self.model, resp["prompt_tokens"], resp["completion_tokens"])

        trace = LLMTrace(
            provider=self.provider,
            model=self.model,
            prompt_tokens=resp["prompt_tokens"],
            completion_tokens=resp["completion_tokens"],
            total_tokens=resp["prompt_tokens"] + resp["completion_tokens"],
            latency_seconds=latency,
            cost_usd=cost,
            cached=False,
        )

        tool_calls = [ToolCall(**tc) for tc in resp.get("tool_calls", [])]
        llm_response = LLMResponse(
            content=resp.get("content", ""),
            tool_calls=tool_calls,
            trace=trace,
        )

        # Store in cache
        if self.enable_cache and self.cache:
            self.cache.set(
                self.model,
                cache_payload,
                response_data={
                    "content": llm_response.content,
                    "tool_calls": [tc.model_dump() for tc in llm_response.tool_calls],
                    "prompt_tokens": resp["prompt_tokens"],
                    "completion_tokens": resp["completion_tokens"],
                },
                prompt_tokens=resp["prompt_tokens"],
                completion_tokens=resp["completion_tokens"],
            )

        return llm_response

    def _complete_openai(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition] | None,
    ) -> dict[str, Any]:
        """Call OpenAI or OpenAI-compatible endpoint."""
        from openai import OpenAI

        client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }

        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
            kwargs["tool_choice"] = "auto"

        response = client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        tool_calls: list[dict[str, Any]] = []
        if choice.message.tool_calls:
            import json

            for tc in choice.message.tool_calls:
                args = {}
                try:
                    args = json.loads(tc.function.arguments)
                except Exception:
                    args = {"raw": tc.function.arguments}
                tool_calls.append(
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": args,
                    }
                )

        usage = response.usage
        p_tokens = usage.prompt_tokens if usage else 0
        c_tokens = usage.completion_tokens if usage else 0

        return {
            "content": choice.message.content or "",
            "tool_calls": tool_calls,
            "prompt_tokens": p_tokens,
            "completion_tokens": c_tokens,
        }

    def _complete_anthropic(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition] | None,
    ) -> dict[str, Any]:
        """Call Anthropic Claude endpoint."""
        import anthropic

        client = anthropic.Anthropic(
            api_key=self.api_key,
            base_url=self.base_url,
        )

        # Separate system message if present
        system_content = ""
        user_messages: list[dict[str, Any]] = []
        for m in messages:
            if m.get("role") == "system":
                system_content += m.get("content", "") + "\n"
            else:
                user_messages.append(m)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": user_messages,
            "temperature": self.temperature,
            "max_tokens": 2048,
        }
        if system_content.strip():
            kwargs["system"] = system_content.strip()

        if tools:
            kwargs["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in tools
            ]

        response = client.messages.create(**kwargs)

        content_text = ""
        tool_calls: list[dict[str, Any]] = []

        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "arguments": block.input,
                    }
                )

        p_tokens = response.usage.input_tokens
        c_tokens = response.usage.output_tokens

        return {
            "content": content_text,
            "tool_calls": tool_calls,
            "prompt_tokens": p_tokens,
            "completion_tokens": c_tokens,
        }

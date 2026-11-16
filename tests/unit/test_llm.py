"""Unit tests for unified LLM client, DiskCache, cost estimation, and provider abstractions."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from planpilot.llm.cache import DiskCache
from planpilot.llm.client import LLMClient, Provider, ToolDefinition
from planpilot.llm.cost import estimate_cost


class TestCostEstimation:
    def test_known_model_pricing(self) -> None:
        """Pricing computes correctly for standard models."""
        # 1M prompt tokens for gpt-4o is $2.50, 1M completion is $10.00
        cost_openai = estimate_cost("gpt-4o", 1000, 1000)
        assert cost_openai == pytest.approx(0.0025 + 0.010, rel=1e-3)

        # Claude 3.5 Sonnet: $3.00 prompt, $15.00 completion per 1M
        cost_claude = estimate_cost("claude-3-5-sonnet-20241022", 1000, 1000)
        assert cost_claude == pytest.approx(0.003 + 0.015, rel=1e-3)

    def test_fallback_pricing(self) -> None:
        """Unrecognized models use safe fallback rates."""
        cost = estimate_cost("custom-finetuned-llama", 1000, 500)
        assert cost > 0.0


class TestDiskCache:
    def test_cache_miss_and_hit(self, tmp_path: Any) -> None:
        """DiskCache correctly saves and retrieves cached payloads."""
        cache_dir = str(tmp_path / "llm_cache")
        cache = DiskCache(cache_dir=cache_dir)

        payload = {"messages": [{"role": "user", "content": "hello"}]}
        model = "gpt-4o"

        # Cache miss
        assert cache.get(model, payload) is None

        # Store in cache
        response_data = {
            "content": "Hello! How can I help with production scheduling?",
            "tool_calls": [],
        }
        cache.set(model, payload, response_data, prompt_tokens=10, completion_tokens=12)

        # Cache hit
        cached = cache.get(model, payload)
        assert cached is not None
        assert cached["content"] == response_data["content"]
        assert cached["prompt_tokens"] == 10
        assert cached["completion_tokens"] == 12

        # Clear cache
        cache.clear()
        assert cache.get(model, payload) is None


class TestLLMClientMocked:
    @patch("openai.OpenAI")
    def test_openai_tool_call_flow(self, mock_openai_cls: MagicMock, tmp_path: Any) -> None:
        """OpenAI provider formats messages, parses tool calls, and traces metrics."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        # Mock OpenAI response
        mock_choice = MagicMock()
        mock_choice.message.content = ""
        mock_tc = MagicMock()
        mock_tc.id = "call_abc123"
        mock_tc.function.name = "propose_rule"
        mock_tc.function.arguments = (
            '{"rule_json": {"type": "pin_to_machine", "order_id": "ORD1", '
            '"operation_id": "OP1", "machine_id": "M1"}}'
        )
        mock_choice.message.tool_calls = [mock_tc]

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 50
        mock_usage.completion_tokens = 25

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage
        mock_client.chat.completions.create.return_value = mock_response

        client = LLMClient(
            provider=Provider.OPENAI,
            model="gpt-4o",
            api_key="sk-test",
            enable_cache=False,
            cache_dir=str(tmp_path / "cache"),
        )

        tool = ToolDefinition(
            name="propose_rule",
            description="Propose a rule",
            parameters={"type": "object", "properties": {"rule_json": {"type": "object"}}},
        )

        res = client.complete(
            messages=[{"role": "user", "content": "Pin OP1 of ORD1 to M1"}],
            tools=[tool],
        )

        assert len(res.tool_calls) == 1
        assert res.tool_calls[0].name == "propose_rule"
        assert res.tool_calls[0].arguments["rule_json"]["type"] == "pin_to_machine"
        assert res.trace.prompt_tokens == 50
        assert res.trace.completion_tokens == 25
        assert res.trace.cost_usd > 0.0
        assert res.trace.cached is False

    @patch("anthropic.Anthropic")
    def test_anthropic_tool_call_flow(self, mock_anthropic_cls: MagicMock, tmp_path: Any) -> None:
        """Anthropic provider separates system prompt, formats tools, and returns tool calls."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        mock_text_block = MagicMock()
        mock_text_block.type = "text"
        mock_text_block.text = "I will propose the rule now."

        mock_tool_block = MagicMock()
        mock_tool_block.type = "tool_use"
        mock_tool_block.id = "toolu_xyz789"
        mock_tool_block.name = "lookup_entities"
        mock_tool_block.input = {"query": "Machine 1", "entity_type": "machine"}

        mock_response = MagicMock()
        mock_response.content = [mock_text_block, mock_tool_block]
        mock_response.usage.input_tokens = 80
        mock_response.usage.output_tokens = 30
        mock_client.messages.create.return_value = mock_response

        client = LLMClient(
            provider=Provider.ANTHROPIC,
            model="claude-3-5-sonnet-20241022",
            api_key="ant-test",
            enable_cache=False,
            cache_dir=str(tmp_path / "cache"),
        )

        tool = ToolDefinition(
            name="lookup_entities",
            description="Look up entities",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        )

        res = client.complete(
            messages=[
                {"role": "system", "content": "You are a helpful scheduling assistant."},
                {"role": "user", "content": "Check Machine 1 status"},
            ],
            tools=[tool],
        )

        # Verify system message was separated for Anthropic API
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["system"] == "You are a helpful scheduling assistant."
        assert len(call_kwargs["messages"]) == 1
        assert call_kwargs["messages"][0]["role"] == "user"

        assert res.content == "I will propose the rule now."
        assert len(res.tool_calls) == 1
        assert res.tool_calls[0].name == "lookup_entities"
        assert res.tool_calls[0].arguments["query"] == "Machine 1"
        assert res.trace.prompt_tokens == 80
        assert res.trace.completion_tokens == 30

    @patch("openai.OpenAI")
    def test_openai_compatible_endpoint(self, mock_openai_cls: MagicMock, tmp_path: Any) -> None:
        """OpenAI-compatible provider uses custom base_url and passes custom model."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        mock_choice = MagicMock()
        mock_choice.message.content = "Open source model response"
        mock_choice.message.tool_calls = []

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 20
        mock_usage.completion_tokens = 10

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage
        mock_client.chat.completions.create.return_value = mock_response

        client = LLMClient(
            provider=Provider.OPENAI_COMPATIBLE,
            model="llama3.1:70b",
            base_url="http://vllm-host:8000/v1",
            api_key="token-custom",
            enable_cache=False,
            cache_dir=str(tmp_path / "cache"),
        )

        res = client.complete(messages=[{"role": "user", "content": "Hello"}])

        assert res.content == "Open source model response"
        # Verify base_url was passed to OpenAI client
        mock_openai_cls.assert_called_once_with(
            api_key="token-custom",
            base_url="http://vllm-host:8000/v1",
        )

    @patch("openai.OpenAI")
    def test_cache_hit_bypasses_provider(self, mock_openai_cls: MagicMock, tmp_path: Any) -> None:
        """Subsequent identical call returns from cache without invoking provider."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        mock_choice = MagicMock()
        mock_choice.message.content = "Original LLM response"
        mock_choice.message.tool_calls = []
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 30
        mock_usage.completion_tokens = 15
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage
        mock_client.chat.completions.create.return_value = mock_response

        client = LLMClient(
            provider=Provider.OPENAI,
            model="gpt-4o",
            api_key="sk-test",
            enable_cache=True,
            cache_dir=str(tmp_path / "cache"),
        )

        messages = [{"role": "user", "content": "Deterministic prompt"}]

        # Call 1: Misses cache, hits provider
        res1 = client.complete(messages)
        assert res1.content == "Original LLM response"
        assert res1.trace.cached is False
        assert mock_client.chat.completions.create.call_count == 1

        # Call 2: Hits cache, provider NOT called again
        res2 = client.complete(messages)
        assert res2.content == "Original LLM response"
        assert res2.trace.cached is True
        assert res2.trace.latency_seconds == 0.0
        assert res2.trace.cost_usd == 0.0
        assert mock_client.chat.completions.create.call_count == 1

    @patch("openai.OpenAI")
    def test_retry_on_transient_error(self, mock_openai_cls: MagicMock, tmp_path: Any) -> None:
        """Tenacity automatically retries transient connection errors."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        mock_choice = MagicMock()
        mock_choice.message.content = "Success after retry"
        mock_choice.message.tool_calls = []
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 5
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        # Fail on attempt 1 with ConnectionError, succeed on attempt 2
        mock_client.chat.completions.create.side_effect = [
            ConnectionError("Temporary network glitch"),
            mock_response,
        ]

        client = LLMClient(
            provider=Provider.OPENAI,
            model="gpt-4o",
            api_key="sk-test",
            enable_cache=False,
            cache_dir=str(tmp_path / "cache"),
        )

        res = client.complete(messages=[{"role": "user", "content": "Retry test"}])
        assert res.content == "Success after retry"
        assert mock_client.chat.completions.create.call_count == 2


class TestLiveSmoke:
    @pytest.mark.live
    def test_live_openai_completion(self) -> None:
        """Live smoke test; executed only if OPENAI_API_KEY is configured."""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            pytest.skip("OPENAI_API_KEY not configured; skipping live smoke test")

        client = LLMClient(provider=Provider.OPENAI, model="gpt-4o-mini", api_key=api_key)
        res = client.complete(
            messages=[{"role": "user", "content": "Reply with exactly the word READY"}]
        )
        assert len(res.content) > 0
        assert res.trace.total_tokens > 0

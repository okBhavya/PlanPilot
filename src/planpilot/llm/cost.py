"""Cost estimation per model and token usage."""

from __future__ import annotations

# Pricing per 1,000,000 tokens (USD)
# (input_price_per_million, output_price_per_million)
PRICING_REGISTRY: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    # Anthropic
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
    "claude-3-5-haiku-20241022": (0.80, 4.00),
    "claude-3-opus-20240229": (15.00, 75.00),
    # Open-source / local (self-hosted)
    "llama3": (0.0, 0.0),
    "llama-3.1-70b": (0.60, 0.60),
    "mistral-7b": (0.0, 0.0),
    "qwen2.5-72b": (0.40, 0.40),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate cost in USD given model and token counts."""
    # Find matching pricing prefix
    pricing = (0.50, 1.50)  # default conservative estimate
    model_lower = model.lower()
    for m, p in PRICING_REGISTRY.items():
        if m in model_lower:
            pricing = p
            break

    input_cost = (prompt_tokens / 1_000_000.0) * pricing[0]
    output_cost = (completion_tokens / 1_000_000.0) * pricing[1]
    return round(input_cost + output_cost, 6)

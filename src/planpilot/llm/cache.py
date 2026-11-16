"""On-disk response cache for reproducible evaluations and lower latency."""

from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class CachedEntry(BaseModel):
    """A single cached LLM response with call metadata."""

    prompt_hash: str
    model: str
    response_data: dict[str, Any]
    prompt_tokens: int
    completion_tokens: int


class DiskCache:
    """Thread-safe on-disk cache for LLM completions."""

    def __init__(self, cache_dir: Path | str = ".cache/llm") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _hash(self, model: str, payload: Any) -> str:
        """Compute SHA256 of normalized input payload."""
        normalized = json.dumps(payload, sort_keys=True, default=str)
        key = f"{model}::{normalized}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def get(self, model: str, payload: Any) -> dict[str, Any] | None:
        """Retrieve cached response if it exists."""
        h = self._hash(model, payload)
        path = self.cache_dir / f"{h}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                res = data.get("response_data")
                if isinstance(res, dict):
                    if "prompt_tokens" not in res and "prompt_tokens" in data:
                        res["prompt_tokens"] = data["prompt_tokens"]
                    if "completion_tokens" not in res and "completion_tokens" in data:
                        res["completion_tokens"] = data["completion_tokens"]
                    return res
                return None
            except Exception:
                return None
        return None

    def set(
        self,
        model: str,
        payload: Any,
        response_data: dict[str, Any],
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        """Store response in cache."""
        h = self._hash(model, payload)
        path = self.cache_dir / f"{h}.json"
        entry = CachedEntry(
            prompt_hash=h,
            model=model,
            response_data=response_data,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        with contextlib.suppress(Exception):
            path.write_text(entry.model_dump_json(indent=2), encoding="utf-8")

    def clear(self) -> None:
        """Remove all cached files."""
        for f in self.cache_dir.glob("*.json"):
            with contextlib.suppress(Exception):
                f.unlink()

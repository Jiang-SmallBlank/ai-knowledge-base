"""Unified LLM client supporting DeepSeek, Qwen, and OpenAI providers.

Usage:
    from pipeline.model_client import quick_chat, create_provider

    response = quick_chat("What is an AI agent?")
    print(response)
"""

import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class Usage:
    """Token usage statistics for an LLM API call.

    Attributes:
        prompt_tokens: Number of tokens in the prompt.
        completion_tokens: Number of tokens in the completion.
        total_tokens: Total tokens consumed.
    """
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMResponse:
    """Response from an LLM provider.

    Attributes:
        content: The generated text content.
        usage: Token usage statistics.
        provider: Name of the provider used.
        model: Model name used.
    """
    content: str
    usage: Usage
    provider: str = ""
    model: str = ""


class CostTracker:
    """Track token usage and cost across LLM API calls.

    Supports per-provider aggregation and summary reporting.
    Prices are in CNY (元) per million tokens.

    Attributes:
        _records: List of recorded API call dicts.
    """

    def __init__(self) -> None:
        """Initialize an empty CostTracker."""
        self._records: list[dict[str, Any]] = []

    def record(self, usage: Usage, provider: str) -> None:
        """Record one API call's token usage and calculated cost.

        Args:
            usage: Token usage statistics from the API response.
            provider: Provider name (e.g. 'deepseek', 'qwen', 'openai').
        """
        cost = calculate_cost(usage, provider)
        self._records.append({
            "provider": provider,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "cost": cost,
        })

    def estimated_cost(self, provider: str | None = None) -> float:
        """Return total estimated cost in CNY.

        Args:
            provider: If set, only sum cost for this provider.

        Returns:
            Total cost in yuan, rounded to 6 decimal places.
        """
        if provider:
            return round(sum(r["cost"] for r in self._records if r["provider"] == provider), 6)
        return round(sum(r["cost"] for r in self._records), 6)

    def report(self, provider: str | None = None) -> None:
        """Print a cost summary to the logger.

        Logs a breakdown of calls, tokens, and cost per provider,
        plus a total line at the end.

        Args:
            provider: If set, only show summary for this provider.
        """
        records = self._records
        if provider:
            records = [r for r in records if r["provider"] == provider]

        if not records:
            print("[CostTracker] No records to report.")
            return

        by_provider: dict[str, dict[str, Any]] = {}
        for r in records:
            p = r["provider"]
            if p not in by_provider:
                by_provider[p] = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
            by_provider[p]["calls"] += 1
            by_provider[p]["prompt_tokens"] += r["prompt_tokens"]
            by_provider[p]["completion_tokens"] += r["completion_tokens"]
            by_provider[p]["cost"] += r["cost"]

        for p, summary in by_provider.items():
            total = summary["prompt_tokens"] + summary["completion_tokens"]
            print(
                f"[CostTracker] {p}: {summary['calls']} call(s), "
                f"{summary['prompt_tokens']} prompt + {summary['completion_tokens']} completion = {total} tokens, "
                f"cost ¥{summary['cost']:.4f}"
            )

        if len(by_provider) > 1:
            total_cost = sum(s["cost"] for s in by_provider.values())
            total_calls = sum(s["calls"] for s in by_provider.values())
            total_tokens = sum(s["prompt_tokens"] + s["completion_tokens"] for s in by_provider.values())
            print(
                f"[CostTracker] TOTAL: {total_calls} call(s), {total_tokens} tokens, "
                f"cost ¥{total_cost:.4f}"
            )


tracker = CostTracker()


PROVIDER_CONFIGS: dict[str, dict[str, str]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "api_key_env": "QWEN_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
    },
}

PRICING: dict[str, dict[str, float]] = {
    "deepseek": {"input": 1, "output": 2},
    "qwen": {"input": 4, "output": 12},
    "openai": {"input": 150, "output": 600},
}

RETRYABLE_STATUSES: set[int] = {429, 500, 502, 503, 504}

DEFAULT_TIMEOUT: int = 60
DEFAULT_MAX_RETRIES: int = 3


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> LLMResponse:
        """Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            **kwargs: Additional parameters (temperature, max_tokens, etc.).

        Returns:
            LLMResponse containing the generated content and usage stats.
        """


class OpenAICompatibleProvider(LLMProvider):
    """LLM provider using OpenAI-compatible API via httpx.

    Attributes:
        api_key: API key for authentication.
        base_url: Base URL of the API endpoint.
        model: Model name to use.
        http_client: Shared httpx client instance.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
    ) -> None:
        """Initialize the provider.

        Args:
            api_key: API key for authentication.
            base_url: Base URL of the API endpoint.
            model: Model name to use.
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.http_client = httpx.Client(
            base_url=self.base_url,
            timeout=DEFAULT_TIMEOUT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    def chat(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> LLMResponse:
        """Send a chat completion request via the OpenAI-compatible API.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            **kwargs: Additional parameters (temperature, max_tokens, etc.).

        Returns:
            LLMResponse containing the generated content and usage stats.

        Raises:
            httpx.HTTPStatusError: On 4xx/5xx responses (except retryable ones
                handled by chat_with_retry).
            httpx.RequestError: On network-level errors.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            **kwargs,
        }

        response = self.http_client.post(
            "/chat/completions",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

        content = data["choices"][0]["message"]["content"]
        usage_data = data.get("usage", {})
        usage = Usage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )

        return LLMResponse(
            content=content,
            usage=usage,
            provider="",
            model=self.model,
        )

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self.http_client.close()


def create_provider(provider_name: str | None = None) -> OpenAICompatibleProvider:
    """Create an LLM provider based on environment configuration.

    Reads LLM_PROVIDER env var (default: 'deepseek') and the corresponding
    API key from the provider-specific env var.

    Args:
        provider_name: Override the provider name. If None, reads from
            LLM_PROVIDER env var.

    Returns:
        An initialized OpenAICompatibleProvider instance.

    Raises:
        ValueError: If the provider is unknown or the API key is missing.
    """
    name = (provider_name or os.getenv("LLM_PROVIDER", "deepseek")).lower()
    config = PROVIDER_CONFIGS.get(name)
    if not config:
        raise ValueError(
            f"Unknown provider '{name}'. "
            f"Supported: {', '.join(PROVIDER_CONFIGS)}"
        )

    api_key = os.getenv(config["api_key_env"])
    if not api_key:
        raise ValueError(
            f"Missing API key: set {config['api_key_env']} "
            f"environment variable"
        )

    logger.info(
        "Creating provider '%s' with model '%s'",
        name, config["model"],
    )
    provider = OpenAICompatibleProvider(
        api_key=api_key,
        base_url=config["base_url"],
        model=config["model"],
    )
    provider.provider_name = name
    return provider


# Monkey-patch for provider name tracking
OpenAICompatibleProvider.provider_name = ""  # type: ignore[attr-defined]


def _should_retry(error: Exception) -> bool:
    """Determine if an error is retryable.

    Retryable: network errors, 429 (rate limit), 5xx (server errors).

    Args:
        error: The exception to check.

    Returns:
        True if the error is retryable, False otherwise.
    """
    if isinstance(error, httpx.RequestError):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in RETRYABLE_STATUSES
    return False


def chat_with_retry(
    provider: LLMProvider,
    messages: list[dict[str, str]],
    max_retries: int = DEFAULT_MAX_RETRIES,
    **kwargs: Any,
) -> LLMResponse:
    """Send a chat request with automatic retry on failure.

    Implements exponential backoff: 1s, 2s, 4s between retries.
    Only retries on network errors, rate limits (429), and server errors (5xx).

    Args:
        provider: An LLMProvider instance.
        messages: List of message dicts with 'role' and 'content' keys.
        max_retries: Maximum number of retry attempts (default: 3).
        **kwargs: Additional parameters passed to provider.chat().

    Returns:
        LLMResponse containing the generated content and usage stats.

    Raises:
        httpx.HTTPStatusError: On non-retryable HTTP errors (4xx except 429).
        httpx.RequestError: If all retries are exhausted.
    """
    last_error: Exception | None = None

    for attempt in range(max_retries):
        try:
            response = provider.chat(messages, **kwargs)
            tracker.record(response.usage, _get_provider_name(provider))
            return response
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise
            if not _should_retry(e):
                raise
            last_error = e
        except httpx.RequestError as e:
            last_error = e
        except Exception as e:
            last_error = e
            if not _should_retry(e):
                raise

        if attempt < max_retries - 1:
            wait = 2**attempt
            logger.warning(
                "Chat failed (attempt %d/%d): %s. Retrying in %ds...",
                attempt + 1, max_retries, last_error, wait,
            )
            time.sleep(wait)

    raise RuntimeError(
        f"Chat failed after {max_retries} retries: {last_error}"
    ) from last_error


def estimate_tokens(text: str) -> int:
    """Estimate token count for a given text.

    Uses a simple heuristic: Chinese characters count as ~1.5 tokens each,
    and non-Chinese words count as ~1.3 tokens each.

    Args:
        text: The input text.

    Returns:
        Estimated number of tokens.
    """
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    words = len(text.split())
    return int(chinese_chars * 1.5 + words * 1.3) + 3  # +3 for overhead


def calculate_cost(
    usage: Usage,
    provider_name: str = "deepseek",
) -> float:
    """Calculate the cost of an API call in CNY (元).

    Uses the PRICING table (元/百万 tokens) for calculation.

    Args:
        usage: Token usage statistics.
        provider_name: Provider name for pricing lookup.

    Returns:
        Cost in yuan, rounded to 6 decimal places.
    """
    pricing = PRICING.get(provider_name, PRICING["deepseek"])
    input_cost = usage.prompt_tokens / 1_000_000 * pricing["input"]
    output_cost = usage.completion_tokens / 1_000_000 * pricing["output"]
    return round(input_cost + output_cost, 6)


def _get_provider_name(provider: LLMProvider) -> str:
    """Extract the provider name from a provider instance."""
    if hasattr(provider, "provider_name") and provider.provider_name:
        return provider.provider_name  # type: ignore[return-value]
    return "deepseek"


def chat(
    prompt: str,
    system: str | None = None,
    provider: LLMProvider | None = None,
    **kwargs: Any,
) -> tuple[str, Usage]:
    """Send a one-shot chat prompt and return (text, usage) tuple.

    Args:
        prompt: The user prompt string.
        system: Optional system prompt string.
        provider: An LLMProvider instance. If None, created via
            create_provider().
        **kwargs: Additional parameters (temperature, max_tokens, etc.).

    Returns:
        A tuple of (response_text, usage).

    Example:
        >>> text, usage = chat("Hello")
    """
    if provider is None:
        provider = create_provider()

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = chat_with_retry(provider, messages, **kwargs)
    return response.content, response.usage


def chat_json(
    prompt: str,
    system: str | None = None,
    provider: LLMProvider | None = None,
    **kwargs: Any,
) -> tuple[dict[str, Any], Usage]:
    """Send a one-shot chat prompt and parse the response as JSON.

    Strips markdown code fences and trailing commas before parsing.

    Args:
        prompt: The user prompt string.
        system: Optional system prompt string.
        provider: An LLMProvider instance. If None, created via
            create_provider().
        **kwargs: Additional parameters (temperature, max_tokens, etc.).

    Returns:
        A tuple of (parsed_json_dict, usage).

    Raises:
        json.JSONDecodeError: If the response cannot be parsed as JSON.
    """
    text, usage = chat(prompt, system, provider, **kwargs)
    text = text.strip()

    m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        text = text[start:end + 1]

    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*\]", "]", text)

    return json.loads(text), usage


def quick_chat(
    prompt: str,
    system: str | None = None,
    provider: LLMProvider | None = None,
    **kwargs: Any,
) -> str:
    """Quick one-shot LLM call returning just the content string.

    Convenience function that wraps message construction and provider
    creation. Uses chat_with_retry internally.

    Args:
        prompt: The user prompt.
        system: Optional system prompt.
        provider: An LLMProvider instance. If None, created via
            create_provider().
        **kwargs: Additional parameters (temperature, max_tokens, etc.).

    Returns:
        The generated text content as a string.

    Example:
        >>> quick_chat("What is an AI agent?")
        'An AI agent is a software program...'
    """
    if provider is None:
        provider = create_provider()

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = chat_with_retry(provider, messages, **kwargs)
    return response.content


def main() -> None:
    """Test the model client with a simple prompt."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    provider_name = os.getenv("LLM_PROVIDER", "deepseek")
    logger.info("Testing provider: %s", provider_name)

    try:
        provider = create_provider(provider_name)
    except ValueError as e:
        logger.error("Provider init failed: %s", e)
        logger.info(
            "Set %s_API_KEY and LLM_PROVIDER env vars to test.",
            provider_name.upper(),
        )
        return

    try:
        response = chat_with_retry(
            provider,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Say exactly 'Hello from <provider_name>!' "
                        "and nothing else."
                    ),
                },
            ],
            temperature=0.0,
            max_tokens=50,
        )
    except Exception as e:
        logger.error("Chat failed: %s", e)
        provider.close()
        return

    logger.info("Response: %s", response.content)

    tracker.report()

    provider.close()


if __name__ == "__main__":
    main()

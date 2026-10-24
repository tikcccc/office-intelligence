"""LLM client wrapper: Xiaomi MiMo and DeepSeek, both through their OpenAI-compatible APIs.

Each task ("role") can use its own model, set in .env as provider:model:
    OFFICE_AGENT_CHAT_LLM=mimo:mimo-v2.6-pro-ultraspeed      answering questions (tool calling)
    OFFICE_AGENT_EXTRACT_LLM=deepseek:deepseek-flash          reading broker reports into figures
    OFFICE_AGENT_BRIEF_LLM=mimo:mimo-v2.6-pro-ultraspeed     writing the weekly brief
    OFFICE_AGENT_EXTRACT_FALLBACK_LLM=deepseek:deepseek-flash  optional: tried if extraction fails
Unset roles use OFFICE_AGENT_MODEL on MiMo.

Docs: https://mimo.mi.com/docs/en-US/quick-start/summary/first-api-call , https://api-docs.deepseek.com
"""

import os
import threading
import time
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from urllib.parse import urlsplit

import openai

from .. import config, runtime
from ..security import safe_text


@dataclass(frozen=True)
class Provider:
    base_url: str
    key_env: str
    token_param: str            # name of the max-output-tokens parameter
    extra_body: dict | None     # provider-specific switches
    echo_reasoning: bool        # whether reasoning_content must be sent back on tool-call turns


PROVIDERS = {
    "mimo": Provider(config.BASE_URL, "MIMO_API_KEY", "max_completion_tokens",
                     {"thinking": {"type": "enabled"}}, echo_reasoning=True),
    "deepseek": Provider("https://api.deepseek.com", "DEEPSEEK_API_KEY", "max_tokens", None, echo_reasoning=False),
}
ROLES = ("chat", "extract", "brief")

# Longest a single call may take, by role. Briefs and extractions write more (and think longer) than chat turns.
ROLE_TIMEOUT_SECONDS = {"chat": 90, "extract": 180, "brief": 240}

# List prices per million tokens (input, output, currency), checked 2026-10-24:
# https://mimo.mi.com/docs/en-US/price/pay-as-you-go , https://api-docs.deepseek.com/quick_start/pricing (peak)
PRICES = {
    "mimo-v2.6-pro-ultraspeed": (30.0, 60.0, "CNY"),
    "mimo-v2.6-pro": (3.0, 6.0, "CNY"),
    "mimo-v2.6-flash": (1.0, 2.0, "CNY"),
    "deepseek-flash": (0.30, 1.20, "USD"),
    "deepseek-v4-pro": (1.32, 3.96, "USD"),
}

_clients: dict[str, openai.OpenAI] = {}
_lock = threading.Lock()
_REQUEST_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="office-model")
_REQUEST_SLOTS = threading.BoundedSemaphore(4)
usage_log: list[dict] = []  # compatibility for experiment scripts; run accounting uses RunContext.calls


class LLMError(RuntimeError):
    pass


def model_for(role: str) -> tuple[str, str]:
    """(provider, model) for a role, from OFFICE_AGENT_<ROLE>_LLM or the MiMo default."""
    spec = os.environ.get(f"OFFICE_AGENT_{role.upper()}_LLM", "")
    if ":" in spec:
        provider, model = spec.split(":", 1)
        if provider in PROVIDERS:
            return provider, model
    return "mimo", config.MODEL


def available(role: str = "chat") -> bool:
    return bool(os.environ.get(PROVIDERS[model_for(role)[0]].key_env))


def configured(role: str) -> bool:
    """True if the role is explicitly set in .env and its key is present (used for optional fallback roles)."""
    return bool(os.environ.get(f"OFFICE_AGENT_{role.upper()}_LLM")) and available(role)


def client(provider: str) -> openai.OpenAI:
    with _lock:
        if provider not in _clients:
            p = PROVIDERS[provider]
            endpoint = urlsplit(p.base_url)
            if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password:
                raise LLMError("Model endpoint must be HTTPS without embedded credentials.")
            # Retries live in the extraction workflow, where the remaining deadline is rechecked.
            _clients[provider] = openai.OpenAI(api_key=os.environ.get(p.key_env), base_url=p.base_url,
                                               max_retries=0)
        return _clients[provider]


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> tuple[float, str] | None:
    """List-price cost of one call (upper bound: ignores cache-hit discounts and DeepSeek off-peak rates)."""
    price = PRICES.get(model)
    if not price:
        return None
    per_in, per_out, currency = price
    return round((input_tokens * per_in + output_tokens * per_out) / 1e6, 5), currency


def chat(messages: list, tools: list | None = None, json_mode: bool = False, max_tokens: int = 16000,
         role: str = "chat", timeout: float | None = None):
    """One chat completion with the role's model; returns the assistant message.
    `timeout` (seconds) caps this call below the role's own limit, e.g. to fit a question's deadline."""
    provider, model = model_for(role)
    try:
        limit = runtime.timeout(min(ROLE_TIMEOUT_SECONDS.get(role.removesuffix("_fallback"), 120),
                                    float("inf") if timeout is None else timeout))
    except runtime.DeadlineExceeded as exc:
        raise LLMError("no time left for another model call.") from exc
    if limit <= 1:
        raise LLMError("no time left for another model call.")
    p = PROVIDERS[provider]
    params = {"model": model, "messages": messages, p.token_param: max_tokens}
    if p.extra_body:
        params["extra_body"] = p.extra_body
    if tools:
        params.update(tools=tools, tool_choice="auto")
    if json_mode:
        params["response_format"] = {"type": "json_object"}
    name = f"{provider}:{model}"
    started = time.time()
    if not _REQUEST_SLOTS.acquire(blocking=False):
        raise LLMError("Model workers are busy; try again shortly.")
    future = None
    try:
        def request():
            runtime.checkpoint()
            return client(provider).with_options(timeout=limit, max_retries=0).chat.completions.create(**params)
        future = runtime.submit(_REQUEST_POOL, request)
        future.add_done_callback(lambda _: _REQUEST_SLOTS.release())
        completion = future.result(timeout=limit)
    except FutureTimeout as exc:
        raise LLMError(f"{name}: no reply within {limit:.0f}s.") from exc
    except openai.APITimeoutError as exc:
        raise LLMError(f"{name}: no reply within {limit:.0f}s.") from exc
    except openai.AuthenticationError as exc:
        raise LLMError(f"{name}: API key missing or invalid (set {p.key_env}).") from exc
    except openai.RateLimitError as exc:
        raise LLMError(f"{name}: rate limit or quota reached; try again shortly.") from exc
    except openai.APIStatusError as exc:
        raise LLMError(f"{name}: API error {exc.status_code}.") from exc
    except openai.APIConnectionError as exc:
        raise LLMError(f"{name}: cannot reach the API (network).") from exc
    finally:
        if future is None:
            _REQUEST_SLOTS.release()
        elif not future.done():
            future.cancel()
    u = completion.usage
    tokens_in, tokens_out = getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0
    cost = estimate_cost(model, tokens_in, tokens_out)
    usage = {"role": role, "model": name, "seconds": round(time.time() - started, 2),
                      "input_tokens": tokens_in, "output_tokens": tokens_out,
                      "cost": cost[0] if cost else None, "currency": cost[1] if cost else None}
    context = runtime.current()
    if context:
        usage["run_id"] = context.run_id
        with context.lock:
            if not context.closed:
                context.calls.append(usage)
    with _lock:
        usage_log.append(usage)
        del usage_log[:-1000]
    runtime.checkpoint()
    if not completion.choices:
        raise LLMError(f"{name}: empty model response.")
    choice = completion.choices[0]
    if choice.finish_reason == "length":
        raise LLMError(f"{name} ran out of output tokens ({max_tokens}) before finishing.")
    return choice.message


def assistant_turn(message, role: str = "chat") -> dict:
    """The assistant message as history. MiMo requires reasoning_content back on tool-call turns."""
    turn = {"role": "assistant", "content": safe_text(message.content or "")}
    reasoning = getattr(message, "reasoning_content", None)
    if reasoning and PROVIDERS[model_for(role)[0]].echo_reasoning:
        turn["reasoning_content"] = safe_text(reasoning)
    if message.tool_calls:
        turn["tool_calls"] = [tc.model_dump() for tc in message.tool_calls]
    return turn

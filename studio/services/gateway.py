"""One model call through Vercel AI Gateway, with the route checked after every call.

The gateway can silently change the route: a failed call on Alex's own key (BYOK) is retried on
Vercel's system credentials, and a request may list fallback models. Neither is allowed to pass
unnoticed. Each call names one model and the credential it must use; the response's routing
metadata must show exactly one model attempt, on that model, that succeeded on that credential.
Anything else is a RouteViolation, raised after the call's cost has been settled in the ledger.
A response whose routing cannot be read at all is a fatal GatewayError, and its reservation
stays at the worst case.

The gateway reports $0 for BYOK calls, because the provider bills them directly, so the ledger
settles the market cost, read from the response or from the generation lookup.

The token comes from VERCEL_OIDC_TOKEN or an env file outside the repository, and it is never
logged, stored in a record or included in an error.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from studio.services.spend import BudgetExceeded, SpendLedger

CHAT_URL = "https://ai-gateway.vercel.sh/v1/chat/completions"
GENERATION_URL = "https://ai-gateway.vercel.sh/v1/generation?id="
PROVIDER = "openai"
TIMEOUT_S = 180
# These statuses would come back for every later call too (rejected token, no credit, unknown
# model), so they stop the run instead of failing it page by page.
FATAL_STATUSES = frozenset({401, 402, 403, 404})
ERROR_CODE = re.compile(r"[a-z0-9_.-]{1,64}")

# USD per token (input, output), from the gateway's model list on October 12, 2026.
PRICES = {
    "openai/gpt-4.1": (2e-6, 8e-6),
    "openai/gpt-6.1-sol": (2e-6, 1e-5),
    "openai/gpt-6-luna": (1e-7, 5e-7),
    "openai/gpt-6-astra": (1e-5, 5e-5),
}
# Reasoning models take a reasoning effort instead of a temperature; the reasoning itself is
# billed as output and is not returned.
REASONING_MODELS = frozenset({"openai/gpt-6.1-sol", "openai/gpt-6-luna", "openai/gpt-6-astra"})
REASONING_EFFORT = "medium"
IMAGE_TOKENS_WORST = 1600  # per page image: a high-detail page tiles to well under this
RESERVE_FACTOR = 1.5  # headroom over the token estimate

Transport = Callable[[str, bytes | None, dict[str, str]], bytes]


class RouteViolation(Exception):
    """The gateway used a model, provider or credential other than the one requested."""


class GatewayError(Exception):
    """The call failed; the message never contains the token or the response body. A fatal
    error would fail every later call the same way, so the run stops."""

    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


@dataclass(frozen=True)
class Route:
    model: str
    credential: str  # "byok" (Alex's key) or "system" (Vercel credits)

    def __post_init__(self):
        if self.model not in PRICES:
            raise ValueError(f"no price for {self.model}; add it before using it")
        if self.credential not in ("byok", "system"):
            raise ValueError(f"credential must be byok or system, got {self.credential}")


@dataclass(frozen=True)
class CallResult:
    content: str
    observed_model: str
    observed_provider: str
    observed_credential: str
    generation_id: str
    input_tokens: int
    output_tokens: int
    gateway_cost_usd: float
    market_cost_usd: float
    seconds: float


def read_token(env_file: Path | None = None) -> str:
    token = os.environ.get("VERCEL_OIDC_TOKEN", "")
    if not token and env_file is not None:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "VERCEL_OIDC_TOKEN":
                token = value
    token = token.strip().strip("'\"")
    if not token:
        raise GatewayError("VERCEL_OIDC_TOKEN is not set (environment or --env-file)")
    return token


def urllib_transport(url: str, body: bytes | None, headers: dict[str, str]) -> bytes:
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        code = _error_code(error)
        detail = f" ({code})" if code else ""
        raise GatewayError(
            f"HTTP {error.code} from the gateway{detail}", fatal=error.code in FATAL_STATUSES
        ) from None
    except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
        raise GatewayError(f"no response from the gateway ({type(error).__name__})") from None


def _error_code(error: urllib.error.HTTPError) -> str:
    """The error's short type or code, if it is a plain identifier. The body itself can echo
    request content, so nothing else from it is kept."""
    try:
        detail = _dict(json.loads(error.read(4096)).get("error"))
    except (ValueError, AttributeError, OSError):
        return ""
    for key in ("type", "code"):
        value = detail.get(key)
        if isinstance(value, str) and ERROR_CODE.fullmatch(value):
            return value
    return ""


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _usd(value) -> float:
    try:
        return max(float(value or 0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def worst_case_usd(route: Route, text_chars: int, images: int, max_output_tokens: int) -> float:
    price_in, price_out = PRICES[route.model]
    input_tokens = text_chars / 3 + images * IMAGE_TOKENS_WORST
    return RESERVE_FACTOR * (input_tokens * price_in + max_output_tokens * price_out)


def request_body(route: Route, messages: list[dict], schema: dict, max_output_tokens: int) -> dict:
    """The full request. Nothing else is sent: no tools, no fallback models, one provider."""
    body = {
        "model": route.model,
        "messages": messages,
        "max_tokens": max_output_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "fields", "strict": True, "schema": schema},
        },
        "providerOptions": {"gateway": {"only": [PROVIDER]}},
    }
    if route.model in REASONING_MODELS:
        body["reasoning"] = {"effort": REASONING_EFFORT, "exclude": True}
    else:
        body["temperature"] = 0
    return body


def _observed(route: Route, response: dict) -> tuple[str, str, str, list[str]]:
    """(model, provider, credential, problems) from the response's routing metadata."""
    gateway = _dict(_dict(_message(response).get("provider_metadata")).get("gateway"))
    model_attempts = _dict(gateway.get("routing")).get("modelAttempts")
    model_attempts = [_dict(a) for a in model_attempts] if isinstance(model_attempts, list) else []
    problems = []
    if len(model_attempts) != 1:
        problems.append(f"{len(model_attempts)} model attempts, expected exactly 1")
    winners = [
        (str(attempt.get("canonicalSlug", "")), _dict(provider))
        for attempt in model_attempts
        for provider in attempt.get("providerAttempts") or []
        if _dict(provider).get("success") is True
    ]
    if len(winners) != 1:
        problems.append(f"{len(winners)} successful provider attempts, expected exactly 1")
        return "", "", "", problems
    model, provider = winners[0]
    observed_model = model
    name, credential = provider.get("provider", ""), provider.get("credentialType", "")
    if observed_model != route.model:
        problems.append(f"model {observed_model} answered, expected {route.model}")
    if name != PROVIDER:
        problems.append(f"provider {name or 'unknown'} answered, expected {PROVIDER}")
    if credential != route.credential:
        problems.append(
            f"credential {credential or 'unknown'} was used, expected {route.credential}"
        )
    return observed_model, name, credential, problems


def _message(response: dict) -> dict:
    return _dict(response["choices"][0]["message"])


def _parse(raw: bytes) -> dict:
    """The response, if it has the one message the route check reads; otherwise fatal."""
    try:
        response = json.loads(raw)
        if isinstance(response, dict) and isinstance(
            _dict(response["choices"][0])["message"], dict
        ):
            return response
    except (ValueError, TypeError, KeyError, IndexError):
        pass
    raise GatewayError("the gateway response has no message, so its route cannot be checked", True)


def call(
    route: Route,
    messages: list[dict],
    schema: dict,
    *,
    token: str,
    ledger: SpendLedger,
    label: str,
    worst_case: float,
    max_output_tokens: int,
    transport: Transport | None = None,
) -> CallResult:
    """Reserve, call, settle, then check the route. Raises BudgetExceeded before any call that
    could cross a cap, and RouteViolation after a call that took a route other than `route`. A
    call that cost more than its reservation is settled at that cost and then raises
    BudgetExceeded; a route violation is reported first."""
    reservation = ledger.reserve(worst_case, label)
    transport = transport or urllib_transport
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = json.dumps(request_body(route, messages, schema, max_output_tokens)).encode()
    started = time.monotonic()
    # A failure or an unreadable response leaves the reservation unsettled, at its worst case.
    response = _parse(transport(CHAT_URL, body, headers))
    seconds = time.monotonic() - started

    usage = _dict(response.get("usage"))
    gateway = _dict(_dict(_message(response).get("provider_metadata")).get("gateway"))
    generation_id = str(response.get("generationId") or gateway.get("generationId") or "")
    gateway_cost = _usd(usage.get("cost") or gateway.get("cost"))
    # BYOK responses can report a market cost of 0; the generation lookup has the real one.
    market = _usd(usage.get("market_cost") or gateway.get("marketCost"))
    if not market and generation_id:
        try:
            lookup = _dict(json.loads(transport(GENERATION_URL + generation_id, None, headers)))
            market = _usd(_dict(lookup.get("data") or lookup).get("market_cost"))
        except (GatewayError, ValueError, TypeError):
            market = 0.0  # unknown: settled at the worst case below
    market_known = market > 0
    market_cost = market if market_known else worst_case  # an unknown cost counts as the worst
    observed_model, provider, credential, problems = _observed(route, response)
    overrun = None
    try:
        ledger.settle(
            reservation,
            max(gateway_cost, market_cost),
            {
                "model": route.model,
                "observed_model": observed_model,
                "credential": credential,
                "generation_id": generation_id,
                "gateway_cost": gateway_cost,
                "market_cost": market_cost,
                "market_cost_known": market_known,
            },
        )
    except BudgetExceeded as error:
        overrun = error
    if problems:
        raise RouteViolation("; ".join(problems))
    if overrun is not None:
        raise overrun
    content = _message(response).get("content")
    return CallResult(
        content=content if isinstance(content, str) else "",
        observed_model=observed_model,
        observed_provider=provider,
        observed_credential=credential,
        generation_id=generation_id,
        input_tokens=int(_usd(usage.get("prompt_tokens"))),
        output_tokens=int(_usd(usage.get("completion_tokens"))),
        gateway_cost_usd=gateway_cost,
        market_cost_usd=market_cost,
        seconds=round(seconds, 2),
    )

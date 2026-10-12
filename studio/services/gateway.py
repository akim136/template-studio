"""One model call through Vercel AI Gateway, with the route checked after every call.

The gateway can silently change the route: a failed call on Alex's own key (BYOK) is retried on
Vercel's system credentials, and a request may list fallback models. Neither is allowed to pass
unnoticed. Each call names one model and the credential it must use; the response's routing
metadata must show exactly one model attempt, on that model, that succeeded on that credential.
Anything else is a RouteViolation, raised after the call's cost has been settled in the ledger.
A response whose routing cannot be read at all is a fatal GatewayError, and its reservation
stays at the worst case.

The gateway reports $0 for BYOK calls, because the provider bills them directly, so the ledger
settles the market cost, read from the response or from the generation lookup. Money is Decimal
from the wire to the ledger.

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
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal, InvalidOperation
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

# The models offered, in USD per token (input, output), from the gateway's model list on
# October 12, 2026. gpt-6-astra is not offered (Alex, October 12, 2026).
PRICES = {
    model: (Decimal(price_in) / 1_000_000, Decimal(price_out) / 1_000_000)
    for model, (price_in, price_out) in {  # USD per million tokens
        "openai/gpt-4.1": ("2", "8"),
        "openai/gpt-5": ("1.25", "10"),
        "openai/gpt-5-mini": ("0.25", "2"),
        "openai/gpt-5-nano": ("0.05", "0.4"),
        "openai/gpt-5.2": ("1.75", "14"),
        "openai/gpt-5.4": ("2.5", "15"),
        "openai/gpt-5.4-mini": ("0.75", "4.5"),
        "openai/gpt-5.4-nano": ("0.2", "1.25"),
        "openai/gpt-5.5": ("5", "30"),
        "openai/gpt-5.6-sol": ("4", "20"),
        "openai/gpt-5.6-terra": ("2", "12"),
        "openai/gpt-5.6-luna": ("0.2", "1.2"),
        "openai/gpt-6.1-sol": ("2", "10"),
        "openai/gpt-6-luna": ("0.1", "0.5"),
    }.items()
}
# Reasoning models take a reasoning effort instead of a temperature; the reasoning itself is
# billed as output and is not returned. Every model offered but gpt-4.1 is one.
REASONING_MODELS = frozenset(PRICES) - {"openai/gpt-4.1"}
REASONING_EFFORT = "medium"
# Per page image at high detail: patch-counted models take up to 2,500 patches at up to 1.5 tokens
# each (gpt-5-nano); tile-counted models (gpt-4.1, gpt-5) stay under 1,500.
IMAGE_TOKENS_WORST = 3750
RESERVE_FACTOR = Decimal("1.5")  # headroom over the token estimate
MICRO = Decimal("0.000001")

Transport = Callable[[str, bytes | None, dict[str, str]], bytes]


class RouteViolation(Exception):
    """The gateway used a model, provider or credential other than the one requested."""


class GatewayError(Exception):
    """The call failed; the message never contains the token or the response body. A fatal
    error would fail every later call the same way, so the run stops."""

    def __init__(self, message: str, fatal: bool = False, status: int | None = None):
        super().__init__(message)
        self.fatal, self.status = fatal, status

    @property
    def refused(self) -> bool:
        """The gateway answered HTTP 4xx: it refused the request, so nothing was generated."""
        return self.status is not None and 400 <= self.status < 500


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
    market_cost_usd: Decimal
    seconds: float
    failed_attempts: list[str] = field(default_factory=list)  # provider/credential/status


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
            f"HTTP {error.code} from the gateway{detail}",
            fatal=error.code in FATAL_STATUSES,
            status=error.code,
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


def _usd(value) -> Decimal:
    """A cost as an exact Decimal; anything missing, negative or unreadable is 0 (unknown)."""
    try:
        usd = Decimal(str(value)) if value is not None else Decimal(0)
    except (InvalidOperation, ValueError):
        return Decimal(0)
    return usd if usd.is_finite() and usd > 0 else Decimal(0)


def _count(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def worst_case_usd(route: Route, text_chars: int, images: int, max_output_tokens: int) -> Decimal:
    """An upper estimate of one call's cost, rounded up to a millionth of a dollar."""
    price_in, price_out = PRICES[route.model]
    input_tokens = Decimal(text_chars) / 3 + images * IMAGE_TOKENS_WORST
    usd = RESERVE_FACTOR * (input_tokens * price_in + max_output_tokens * price_out)
    return usd.quantize(MICRO, rounding=ROUND_CEILING)


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


def _observed(route: Route, response: dict) -> tuple[str, str, str, list[str], list[str]]:
    """(model, provider, credential, failed attempts, problems) from the routing metadata.

    Every attempt, failed or not, must be on the pinned provider. A failed attempt on the other
    credential is recorded, not refused: with the system credential the gateway tries Alex's key
    first, and that fails for every model his key cannot reach."""
    gateway = _dict(_dict(_message(response).get("provider_metadata")).get("gateway"))
    model_attempts = _dict(gateway.get("routing")).get("modelAttempts")
    model_attempts = [_dict(a) for a in model_attempts] if isinstance(model_attempts, list) else []
    problems = []
    if len(model_attempts) != 1:
        problems.append(f"{len(model_attempts)} model attempts, expected exactly 1")
    attempts = [
        (str(attempt.get("canonicalSlug", "")), _dict(provider))
        for attempt in model_attempts
        for provider in _list(attempt.get("providerAttempts"))
    ]
    others = sorted({str(p.get("provider") or "unknown") for _, p in attempts} - {PROVIDER})
    if others:
        problems.append(f"provider {', '.join(others)} attempted, expected {PROVIDER} only")
    failed = [
        f"{p.get('provider') or 'unknown'}/{p.get('credentialType') or 'unknown'}/"
        f"{p.get('statusCode', '')}"
        for _, p in attempts
        if p.get("success") is not True
    ]
    winners = [(slug, p) for slug, p in attempts if p.get("success") is True]
    if len(winners) != 1:
        problems.append(f"{len(winners)} successful provider attempts, expected exactly 1")
        return "", "", "", failed, problems
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
    return observed_model, name, credential, failed, problems


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _message(response: dict) -> dict:
    return _dict(response["choices"][0]["message"])


def _parse(raw: bytes) -> dict:
    """The response, if it has the one message the route check reads; otherwise fatal."""
    try:
        response = json.loads(raw, parse_float=Decimal)
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
    # A refused call settles at $0 (Alex, October 12, 2026). Any other failure, or an unreadable
    # response, leaves the reservation unsettled, at its worst case.
    try:
        raw = transport(CHAT_URL, body, headers)
    except GatewayError as error:
        if error.refused:
            ledger.settle(reservation, Decimal(0), {"model": route.model, "refused": str(error)})
        raise
    response = _parse(raw)
    seconds = time.monotonic() - started

    usage = _dict(response.get("usage"))
    gateway = _dict(_dict(_message(response).get("provider_metadata")).get("gateway"))
    generation_id = str(response.get("generationId") or gateway.get("generationId") or "")
    gateway_cost = _usd(usage.get("cost") or gateway.get("cost"))
    # BYOK responses can report a market cost of 0; the generation lookup has the real one.
    market = _usd(usage.get("market_cost") or gateway.get("marketCost"))
    if not market and generation_id:
        try:
            reply = transport(GENERATION_URL + generation_id, None, headers)
            lookup = _dict(json.loads(reply, parse_float=Decimal))
            market = _usd(_dict(lookup.get("data") or lookup).get("market_cost"))
        except (GatewayError, ValueError, TypeError):
            market = Decimal(0)  # unknown: settled at the worst case below
    market_known = market > 0
    market_cost = market if market_known else _usd(worst_case)  # unknown counts as the worst
    observed_model, provider, credential, failed, problems = _observed(route, response)
    overrun = None
    try:
        ledger.settle(
            reservation,
            max(gateway_cost, market_cost),
            {
                "model": route.model,
                "observed_model": observed_model,
                "credential": credential,
                "failed_attempts": failed,
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
        input_tokens=_count(usage.get("prompt_tokens")),
        output_tokens=_count(usage.get("completion_tokens")),
        market_cost_usd=market_cost,
        seconds=round(seconds, 2),
        failed_attempts=failed,
    )

"""A stand-in for Vercel AI Gateway. Responses follow the shape the real gateway returned in the
October 12, 2026 smoke test; no network call is made."""

import json
import re

from studio.services import gateway


def response(
    content: str,
    *,
    model="openai/gpt-4.1",
    attempts=None,
    model_attempts=1,
    cost=0.0,
    market=0.002,
    generation="gen_test",
):
    attempts = attempts or [
        {"provider": "openai", "credentialType": "byok", "success": True, "statusCode": 200}
    ]
    routing = {
        "modelAttempts": [
            {"canonicalSlug": model, "success": True, "providerAttempts": attempts}
            for _ in range(model_attempts)
        ]
    }
    return {
        "id": "chatcmpl-test",
        "generationId": generation,
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                    "provider_metadata": {"gateway": {"routing": routing}},
                }
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "cost": cost,
            "market_cost": market,
        },
    }


def first_candidate_answer(body: dict) -> dict:
    """A signature on the first listed candidate, or no fields; the shape of either stage."""
    content = body["messages"][-1]["content"]
    text = content[0]["text"] if isinstance(content, list) else content
    match = re.search(r"^(p\d+-\d+) ", text, re.M)
    fields = []
    if match:
        fields.append(
            {
                "candidate_id": match.group(1),
                "type": "signature",
                "role": "Client",
                "merge_name": None,
                "required": True,
                "box": {"left": 10, "top": 10, "width": 100, "height": 20},
                "evidence": "Signature",
            }
        )
    answer = {"fields": fields, "notes": ""}
    if "changes" in body["response_format"]["json_schema"]["schema"]["properties"]:
        answer["changes"] = []
    return answer


class FakeGateway:
    def __init__(self, answer=first_candidate_answer, fail_stage_b=False, lookup=0.003, **kw):
        self.answer, self.fail_stage_b, self.lookup, self.kw = answer, fail_stage_b, lookup, kw
        self.requests: list[tuple[str, dict | None, dict]] = []

    def __call__(self, url: str, body: bytes | None, headers: dict) -> bytes:
        payload = json.loads(body) if body else None
        self.requests.append((url, payload, headers))
        if url.startswith(gateway.GENERATION_URL):
            return json.dumps({"data": {"market_cost": self.lookup}}).encode()
        schema = payload["response_format"]["json_schema"]["schema"]
        if self.fail_stage_b and "changes" in schema["properties"]:
            raise gateway.GatewayError("HTTP 500 from the gateway: synthetic failure")
        return json.dumps(response(json.dumps(self.answer(payload)), **self.kw)).encode()

    def chat_bodies(self) -> list[dict]:
        return [body for url, body, _ in self.requests if url == gateway.CHAT_URL]

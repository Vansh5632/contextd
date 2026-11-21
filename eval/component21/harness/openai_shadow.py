"""OpenAI structured-output adapter for the shadow classifier.

This module does not open SQLite and does not import the daemon. Constructing
a client without ``authorized=True`` raises before any network call.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Protocol

from harness.shadow import ShadowRequest, ShadowVerdict
from harness.shadow_experiment import SpendNotAuthorized, build_request_payload, parse_structured_output


class ChatTransport(Protocol):
    def complete(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class UrllibChatTransport:
    """HTTPS call to the OpenAI chat completions endpoint. No database handle."""

    def __init__(self, api_key: str, *, timeout_s: float = 60.0) -> None:
        self._api_key = api_key
        self._timeout_s = timeout_s

    def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
            return json.loads(response.read().decode())


class OpenAIShadow:
    """ShadowClassifier that calls OpenAI only after spend is authorized.

    Verdicts still go through ``ShadowVerdict``, which refuses ``writes_memory``.
    """

    def __init__(self, transport: ChatTransport, model: str, *, authorized: bool) -> None:
        if not authorized:
            raise SpendNotAuthorized("refusing OpenAI call; pass --i-accept-spend to authorize")
        self._transport = transport
        self.model = model
        self.records: list[dict[str, Any]] = []

    def classify(self, request: ShadowRequest) -> ShadowVerdict:
        payload = build_request_payload(
            self.model,
            {"claim_text": request.claim_text, "evidence_text": request.evidence_text},
        )
        body = self._transport.complete(payload)
        parsed = parse_structured_output(body)
        self.records.append(parsed)
        return ShadowVerdict(parsed["label"], parsed["evidence_quote"])

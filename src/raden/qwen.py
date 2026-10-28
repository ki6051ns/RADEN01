"""One shared OpenAI-compatible model endpoint, independent role requests."""
import json
import math
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

from .core import Decision, json_default
from .prompts import DECISION_SCHEMA, messages


@dataclass
class QwenBackend:
    endpoint: str
    model: str = "Qwen/Qwen3-8B"
    api_key: str | None = None
    timeout: float = 30
    context_events: int = 100
    max_tokens: int = 512

    def __post_init__(self):
        url = urlsplit(self.endpoint)
        if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("Qwen endpoint requires HTTPS or local HTTP")
        if url.username or url.password or not url.hostname or url.query or url.fragment:
            raise ValueError("invalid Qwen endpoint")
        if self.context_events < 1 or self.max_tokens < 1 or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("context, output budget and timeout must be positive")

    def decide(self, role, history):
        return self.decide_with_state(role, history, {})

    def decide_with_state(self, role, history, portfolio):
        # Decimal accounting values are serialized, never used to transform raw observations.
        state = json.loads(json.dumps(portfolio, default=json_default))
        payload = {
            "model": self.model, "temperature": 0, "max_tokens": self.max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "paper_decision", "strict": True, "schema": DECISION_SCHEMA}},
            "messages": messages(role, history, self.context_events, state),
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = urllib.request.Request(self.endpoint, data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            result = json.load(response)
        tokens = result.get("usage", {}).get("prompt_tokens")
        if type(tokens) is not int or tokens < 0:
            tokens = None
        source = "server_usage" if tokens is not None else "unavailable"
        try:
            choice = result["choices"][0]
            if choice.get("finish_reason") not in (None, "stop"):
                raise ValueError("incomplete response")
            content = json.loads(choice["message"]["content"])
            if not isinstance(content, dict) or set(content) != {"action", "reason"}:
                raise ValueError("invalid decision schema")
            return Decision(content["action"], content["reason"], tokens, source, self.model)
        except (ValueError, TypeError, KeyError, IndexError):
            return Decision("hold", "backend error: invalid response", tokens, source, self.model, "error")

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class PublicApiError(RuntimeError):
    """Raised when a public provider request cannot be completed safely."""


@dataclass
class JsonHttpClient:
    timeout_seconds: float = 15.0
    retries: int = 2
    user_agent: str = "crypto-simulator/0.1"

    def request(self, method: str, url: str, body: dict | None = None) -> object:
        encoded = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers = {"Accept": "application/json", "User-Agent": self.user_agent}
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                request = Request(url, data=encoded, headers=headers, method=method.upper())
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(0.5 * (attempt + 1))
        raise PublicApiError(f"public API request failed: {method} {url}: {last_error}") from last_error

    def get(self, url: str) -> object:
        return self.request("GET", url)

    def post(self, url: str, body: dict) -> object:
        return self.request("POST", url, body)

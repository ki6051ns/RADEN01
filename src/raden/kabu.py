"""Read-only kabu Station board polling. There is no order/authentication endpoint."""
import json
import math
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

from .core import MarketEvent, positive_price


@dataclass
class KabuBoardSource:
    symbols: tuple[str, ...]
    token: str
    base_url: str = "http://127.0.0.1:18080/kabusapi"
    polls: int = 1
    interval: float = 1
    timeout: float = 10

    def __post_init__(self):
        url = urlsplit(self.base_url)
        if url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("kabu adapter requires local HTTP; use an authenticated local tunnel")
        if url.username or url.password or url.query or url.fragment or url.path.rstrip("/") != "/kabusapi":
            raise ValueError("invalid kabu base URL")
        if not self.token or not self.symbols or self.polls < 1:
            raise ValueError("token, symbols and positive poll count required")
        if any(not s or any(c not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" for c in s) for s in self.symbols):
            raise ValueError("invalid symbol")
        if not math.isfinite(self.interval) or self.interval < 0 or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("invalid polling timing")

    def __iter__(self):
        for cycle in range(self.polls):
            if cycle:
                time.sleep(self.interval)
            for symbol in self.symbols:
                # Only GET /board is reachable from this adapter. Token is externally supplied.
                request = urllib.request.Request(
                    self.base_url.rstrip("/") + "/board/" + quote(symbol, safe="") + "@1",
                    headers={"X-API-KEY": self.token}, method="GET")
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    original = response.read().decode("utf-8")
                received = datetime.now(timezone.utc)
                board = json.loads(original)
                if str(board.get("Symbol")) != symbol:
                    raise ValueError("kabu returned wrong symbol")
                price = positive_price(str(board["CurrentPrice"]))
                observed = datetime.fromisoformat(board["CurrentPriceTime"])
                if observed.tzinfo is None or observed.utcoffset() is None or observed > received:
                    raise ValueError("invalid or future kabu price timestamp")
                # Availability time drives replay ordering; preserve board bytes as decoded JSON text.
                yield MarketEvent(received, symbol, {"board_json": original}, price)

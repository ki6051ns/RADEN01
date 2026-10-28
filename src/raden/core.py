from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping, Protocol, TextIO


def positive_price(value: str) -> Decimal:
    price = Decimal(value)
    if not price.is_finite() or price <= 0:
        raise ValueError("price must be finite and positive")
    return price


@dataclass(frozen=True)
class MarketEvent:
    timestamp: datetime
    symbol: str
    raw: Mapping[str, str]

    def __post_init__(self):
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must include timezone")
        if not self.symbol:
            raise ValueError("symbol is required")
        object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))


class MarketDataSource(Protocol):
    """A future kabu feed implements this interface; it never submits orders."""

    def __iter__(self) -> Iterable[MarketEvent]: ...


@dataclass
class CSVReplay:
    path: Path

    def __iter__(self):
        previous = None
        with self.path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            if not {"timestamp", "symbol", "price"} <= set(reader.fieldnames or []):
                raise ValueError("CSV requires timestamp,symbol,price")
            if len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise ValueError("duplicate CSV columns")
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("malformed CSV row")
                event = MarketEvent(datetime.fromisoformat(row["timestamp"]), row["symbol"], row)
                positive_price(row["price"])
                if previous is not None and event.timestamp < previous:
                    raise ValueError("CSV must be globally chronological")
                previous = event.timestamp
                yield event


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str

    def __post_init__(self):
        if self.action not in {"buy", "sell", "hold"} or not isinstance(self.reason, str):
            raise ValueError("invalid decision")


class DecisionBackend(Protocol):
    def decide(self, role: str, history: tuple[MarketEvent, ...]) -> Decision: ...


ROLES = (
    "market-observer", "liquidity-observer", "volume-observer", "price-observer",
    "entry-reviewer", "exit-reviewer", "risk-reviewer", "execution-reviewer",
    "counterargument-reviewer", "portfolio-reviewer",
)


@dataclass
class MockBackend:
    """Deterministic plumbing fixture, not an investment strategy."""

    def decide(self, role, history):
        action = "hold"
        if role == "entry-reviewer" and len(history) == 1:
            action = "buy"
        elif role == "exit-reviewer" and len(history) == 3:
            action = "sell"
        return Decision(action, "synthetic replay fixture")


@dataclass
class PaperBroker:
    cash: Decimal = Decimal("1000000")
    positions: dict[str, int] = field(default_factory=dict)
    marks: dict[str, Decimal] = field(default_factory=dict)
    pending: list[dict] = field(default_factory=list)
    initial_cash: Decimal = field(init=False)
    realized_pnl: Decimal = Decimal("0")
    costs: dict[str, Decimal] = field(default_factory=dict)

    def __post_init__(self):
        if not self.cash.is_finite() or self.cash < 0:
            raise ValueError("cash must be finite and nonnegative")
        self.initial_cash = self.cash

    def on_event(self, event: MarketEvent) -> list[dict]:
        price = positive_price(event.raw["price"])
        self.marks[event.symbol] = price
        results = []
        remaining = []
        for order in self.pending:
            if order["symbol"] != event.symbol or event.timestamp <= order["submitted_at"]:
                remaining.append(order)
                continue
            symbol, action = order["symbol"], order["action"]
            quantity = self.positions.get(symbol, 0)
            status = "rejected"
            if action == "buy" and self.cash >= price:
                self.cash -= price
                self.positions[symbol] = quantity + 1
                self.costs[symbol] = self.costs.get(symbol, Decimal(0)) + price
                status = "filled"
            elif action == "sell" and quantity > 0:
                cost = self.costs[symbol] / quantity
                self.realized_pnl += price - cost
                self.costs[symbol] -= cost
                self.cash += price
                self.positions[symbol] = quantity - 1
                status = "filled"
            results.append({**order, "type": "execution", "status": status,
                            "timestamp": event.timestamp, "price": price})
        self.pending = remaining
        return results

    def submit(self, event, action):
        order = {"id": self._next_id(), "symbol": event.symbol, "action": action,
                 "quantity": 1, "submitted_at": event.timestamp}
        self.pending.append(order)
        return {**order, "type": "order", "status": "pending"}

    def _next_id(self):
        self._sequence = getattr(self, "_sequence", 0) + 1
        return self._sequence

    def valuation(self):
        equity = self.cash + sum((self.marks[s] * q for s, q in self.positions.items()), Decimal(0))
        return {"cash": self.cash, "equity": equity, "pnl": equity - self.initial_cash,
                "realized_pnl": self.realized_pnl,
                "unrealized_pnl": equity - self.initial_cash - self.realized_pnl,
                "positions": dict(self.positions), "pending_orders": len(self.pending)}


def json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def run(source: MarketDataSource, backend: DecisionBackend, output: TextIO,
        broker: PaperBroker | None = None):
    broker = broker if broker is not None else PaperBroker()
    histories: dict[str, list[MarketEvent]] = {}
    previous = None

    def write(record):
        output.write(json.dumps(record, default=json_default, ensure_ascii=False) + "\n")
        output.flush()

    for event in source:
        if previous is not None and event.timestamp < previous:
            raise ValueError("source must be globally chronological")
        previous = event.timestamp
        write({"type": "market", "timestamp": event.timestamp, "symbol": event.symbol,
               "raw": dict(event.raw)})
        for execution in broker.on_event(event):
            write(execution)
        history = histories.setdefault(event.symbol, [])
        history.append(event)
        decisions = []
        for role in ROLES:
            started = time.monotonic()
            try:
                decision = backend.decide(role, tuple(history))
                if not isinstance(decision, Decision):
                    raise ValueError("backend must return Decision")
            except Exception as error:
                # Do not log exception text: remote responses may contain secrets.
                decision = Decision("hold", "backend error: " + type(error).__name__)
            decisions.append(decision)
            write({"type": "decision", "timestamp": event.timestamp, "symbol": event.symbol,
                   "agent": role, "action": decision.action, "reason": decision.reason,
                   "latency_ms": (time.monotonic() - started) * 1000})
        # One paper order per symbol/event. Ties and all-hold mean no order.
        buys = sum(d.action == "buy" for d in decisions)
        sells = sum(d.action == "sell" for d in decisions)
        if buys != sells:
            write(broker.submit(event, "buy" if buys > sells else "sell"))
        write({"type": "valuation", "timestamp": event.timestamp, **broker.valuation()})
    summary = broker.valuation()
    write({"type": "summary", **summary})
    return summary

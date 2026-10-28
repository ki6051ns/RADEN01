import io
import json
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from raden.core import CSVReplay, Decision, MarketEvent, MockBackend, PaperBroker, ROLES, run
from raden.qwen import QwenBackend


def event(second, symbol="A", price="100"):
    return MarketEvent(datetime.fromisoformat(f"2026-01-01T00:00:0{second}+00:00"),
                       symbol, {"price": price, "untouched": "001.00"})


class ReplayTests(unittest.TestCase):
    def test_synthetic_end_to_end(self):
        output = io.StringIO()
        summary = run(CSVReplay(Path("examples/synthetic.csv")), MockBackend(), output)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len([r for r in records if r["type"] == "decision"]), 40)
        self.assertEqual(summary["pnl"], Decimal(3))
        self.assertEqual(summary["realized_pnl"], Decimal(3))
        self.assertEqual(summary["positions"], {"DEMO": 0})
        executions = [r for r in records if r["type"] == "execution"]
        self.assertEqual([r["price"] for r in executions], ["101", "104"])

    def test_prefix_only_per_symbol_and_immutable_raw(self):
        seen = []
        class Spy:
            def decide(self, role, history):
                seen.append((role, history))
                with self_test.assertRaises(TypeError):
                    history[-1].raw["price"] = "0"
                return Decision("hold", "test")
        self_test = self
        events = [event(0), event(1, "B"), event(2)]
        run(events, Spy(), io.StringIO())
        self.assertEqual(len(ROLES), 10)
        self.assertEqual(len(set(ROLES)), 10)
        for index, (_, history) in enumerate(seen):
            current = events[index // 10]
            self.assertTrue(all(e.timestamp <= current.timestamp for e in history))
            self.assertTrue(all(e.symbol == current.symbol for e in history))
            self.assertEqual(history[-1].raw["untouched"], "001.00")
        self.assertEqual(len(seen[0][1]), 1)
        self.assertEqual(len(seen[-1][1]), 2)

    def test_orders_wait_for_later_timestamp_same_symbol(self):
        broker = PaperBroker()
        broker.submit(event(0), "buy")
        self.assertEqual(broker.on_event(event(0)), [])
        self.assertEqual(broker.on_event(event(1, "B")), [])
        self.assertEqual(broker.on_event(event(2))[0]["status"], "filled")

    def test_reject_short_and_insufficient_cash(self):
        broker = PaperBroker(Decimal(0))
        broker.submit(event(0), "sell")
        broker.submit(event(0), "buy")
        self.assertEqual([r["status"] for r in broker.on_event(event(1))], ["rejected", "rejected"])
        self.assertEqual(broker.valuation()["equity"], 0)

    def test_end_of_feed_keeps_orders_pending(self):
        result = run([event(0)], MockBackend(), io.StringIO())
        self.assertEqual(result["pending_orders"], 1)
        self.assertEqual(result["positions"], {})

    def test_backend_failure_holds_and_redacts_error(self):
        class Broken:
            def decide(self, *args):
                raise RuntimeError("secret-value")
        output = io.StringIO()
        result = run([event(0)], Broken(), output)
        self.assertEqual(result["pending_orders"], 0)
        self.assertNotIn("secret-value", output.getvalue())
        self.assertEqual(output.getvalue().count("backend error"), 10)

    def test_csv_validation_and_raw_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.csv"
            path.write_text("timestamp,symbol,price,extra\n2026-01-01T00:00:00Z,A,001.00, raw \n")
            self.assertEqual(list(CSVReplay(path))[0].raw["extra"], " raw ")
            self.assertEqual(list(CSVReplay(path))[0].raw["price"], "001.00")
            for body in [
                "timestamp,symbol\n2026-01-01,A\n",
                "timestamp,symbol,price\n2026-01-01T00:00:00,A,1\n",
                "timestamp,symbol,price\n2026-01-01T00:00:00Z,A,NaN\n",
                "timestamp,symbol,price\n2026-01-01T00:00:00Z,A,-1\n",
                "timestamp,symbol,price\n2026-01-01T00:00:00Z,A,1,extra\n",
                "timestamp,symbol,price\n2026-01-01T00:00:01Z,A,1\n2026-01-01T00:00:00Z,A,2\n",
            ]:
                with self.subTest(body=body):
                    path.write_text(body)
                    with self.assertRaises(ValueError):
                        list(CSVReplay(path))

    def test_non_csv_source_cannot_go_backwards(self):
        with self.assertRaises(ValueError):
            run([event(1), event(0)], MockBackend(), io.StringIO())

    def test_qwen_payload_and_response(self):
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content":
            '{"action":"hold","reason":"observed"}'}}]}).encode())
        with patch("urllib.request.urlopen", return_value=response) as request:
            decision = QwenBackend("https://example.com/v1/chat/completions", "qwen-test").decide("risk", (event(0),))
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload["model"], "qwen-test")
        self.assertEqual(json.loads(payload["messages"][1]["content"])[0]["untouched"], "001.00")
        self.assertEqual(decision.action, "hold")

    def test_invalid_qwen_decision_and_insecure_endpoint(self):
        with self.assertRaises(ValueError):
            Decision("execute-live", "bad")
        with self.assertRaises(ValueError):
            QwenBackend("http://remote.example/v1/chat/completions", "qwen")


if __name__ == "__main__":
    unittest.main()

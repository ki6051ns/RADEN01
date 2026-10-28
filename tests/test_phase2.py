import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from raden.core import CSVReplay, Decision, MockBackend, ROLES, run
from raden.kabu import KabuBoardSource
from raden.prompts import ROLE_PROMPTS
from raden.qwen import QwenBackend
from test_replay import event


class Phase2Tests(unittest.TestCase):
    def test_http_inference_all_roles_usage_state_and_execution(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(payload)
                system = payload["messages"][0]["content"]
                history = json.loads(payload["messages"][1]["content"])
                action = "hold"
                if "entry-reviewer agent." in system and len(history) == 1:
                    action = "buy"
                if "exit-reviewer agent." in system and len(history) == 3:
                    action = "sell"
                body = json.dumps({"choices": [{"finish_reason": "stop", "message": {
                    "content": json.dumps({"action": action, "reason": "fixture"})}}],
                    "usage": {"prompt_tokens": 123}}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            output = io.StringIO()
            result = run(CSVReplay(Path("examples/synthetic.csv")),
                         QwenBackend(f"http://127.0.0.1:{server.server_port}/v1/chat/completions"), output)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
        self.assertEqual(str(result["pnl"]), "3")
        self.assertEqual(len(requests), 40)
        self.assertEqual(len(set(p["messages"][0]["content"] for p in requests[:10])), 10)
        self.assertTrue(all(p["chat_template_kwargs"] == {"enable_thinking": False} for p in requests))
        self.assertIn('"DEMO": 1', requests[10]["messages"][0]["content"])
        decisions = [json.loads(s) for s in output.getvalue().splitlines() if json.loads(s)["type"] == "decision"]
        self.assertTrue(all(d["input_tokens"] == 123 and d["token_source"] == "server_usage" for d in decisions))
        self.assertTrue(all(d["latency_ms"] >= 0 and d["status"] == "ok" for d in decisions))

    def test_invalid_responses_hold_and_preserve_usage(self):
        for content, finish in [("not json", "stop"), ('{"action":"buy","reason":"x"}', "length"),
                                ('{"action":"live","reason":"x"}', "stop"),
                                ('{"action":"buy","reason":"x","extra":1}', "stop")]:
            with self.subTest(content=content, finish=finish):
                response = io.BytesIO(json.dumps({"choices": [{"finish_reason": finish,
                    "message": {"content": content}}], "usage": {"prompt_tokens": 77}}).encode())
                with patch("urllib.request.urlopen", return_value=response):
                    decision = QwenBackend("http://localhost:8000/v1/chat/completions").decide(ROLES[0], (event(0),))
                self.assertEqual((decision.action, decision.status, decision.input_tokens), ("hold", "error", 77))

    def test_mock_metrics_and_raw_separation(self):
        output = io.StringIO()
        run([event(0)], MockBackend(), output)
        records = [json.loads(s) for s in output.getvalue().splitlines()]
        market = records[0]
        self.assertEqual(market["type"], "market")
        self.assertNotIn("action", market)
        decisions = [r for r in records if r["type"] == "decision"]
        self.assertEqual(set(r["agent"] for r in decisions), set(ROLE_PROMPTS))
        self.assertTrue(all(r["input_tokens"] > 0 and "estimate" in r["token_source"] for r in decisions))
        self.assertTrue(all("raw" not in r for r in decisions))

    def test_telemetry_summary_distinguishes_missing_usage(self):
        from raden.benchmark import summarize
        class Broken:
            model = "unreachable-qwen"
            def decide(self, *args):
                raise TimeoutError("private-response")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.jsonl"
            with path.open("w") as output:
                run([event(0)], Broken(), output)
            metrics = summarize(path)
        self.assertEqual(len(metrics), 10)
        self.assertTrue(all(m["errors"] == 1 and m["token_calls"] == 0 for m in metrics))
        self.assertTrue(all(m["model"] == "unreachable-qwen" for m in metrics))

    def test_window_and_future_suffix_independence(self):
        class Spy:
            def __init__(self):
                self.inputs = []
            def decide(self, role, history):
                self.inputs.append((role, history))
                return Decision("hold", "observed")
        first, second = Spy(), Spy()
        run([event(0), event(1), event(2)], first, io.StringIO(), context_events=2)
        run([event(0), event(1), event(2, price="999")], second, io.StringIO(), context_events=2)
        self.assertEqual(first.inputs[:20], second.inputs[:20])
        self.assertEqual([e.timestamp for e in first.inputs[-1][1]], [event(1).timestamp, event(2).timestamp])

    def test_kabu_get_only_and_original_payload(self):
        original = '{ "Symbol":"7203", "CurrentPrice":100.0, "CurrentPriceTime":"2026-01-01T09:00:00+09:00", "Buy1":{"Qty":10} }'
        with patch("urllib.request.urlopen", side_effect=lambda *a, **k: io.BytesIO(original.encode())) as request:
            events = list(KabuBoardSource(("7203",), "test-token", polls=2, interval=0))
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e.raw == {"board_json": original} for e in events))
        self.assertEqual(str(events[0].execution_price), "100.0")
        for call in request.call_args_list:
            self.assertEqual(call.args[0].get_method(), "GET")
            self.assertEqual(call.args[0].full_url, "http://127.0.0.1:18080/kabusapi/board/7203@1")
        output = io.StringIO()
        result = run(events, MockBackend(), output)
        self.assertEqual(result["positions"], {"7203": 1})
        self.assertNotIn("test-token", output.getvalue())

    def test_kabu_rejects_future_wrong_symbol_and_invalid_price(self):
        for updates in [{"CurrentPriceTime": "2999-01-01T00:00:00+00:00"},
                        {"Symbol": "OTHER"}, {"CurrentPrice": -1}]:
            board = {"Symbol": "7203", "CurrentPrice": 100, "CurrentPriceTime": "2026-01-01T00:00:00+00:00", **updates}
            with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(board).encode())):
                with self.assertRaises(ValueError):
                    list(KabuBoardSource(("7203",), "test"))
        for url in ["https://remote/kabusapi", "http://localhost/sendorder", "http://localhost/kabusapi?token=x"]:
            with self.assertRaises(ValueError):
                KabuBoardSource(("7203",), "test", base_url=url)

    def test_cli_mock_no_gpu_and_preserves_existing_log(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "replay.jsonl"
            command = [sys.executable, "-m", "raden.cli", "examples/synthetic.csv", "--output", str(output)]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(first.stdout)["pnl"], "3")
            before = output.read_bytes()
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual(output.read_bytes(), before)

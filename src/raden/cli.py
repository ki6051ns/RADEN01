import argparse
import json
import os
from decimal import Decimal
from pathlib import Path

from .core import CSVReplay, MockBackend, PaperBroker, json_default, run
from .qwen import QwenBackend
from .kabu import KabuBoardSource


def main():
    parser = argparse.ArgumentParser(description="RADEN01 paper-only Qwen farm")
    parser.add_argument("csv", type=Path, nargs="?")
    parser.add_argument("--source", choices=["csv", "kabu"], default="csv")
    parser.add_argument("--symbols", help="comma-separated TSE symbols for read-only kabu polling")
    parser.add_argument("--polls", type=int, default=1)
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--context-events", type=int, default=100)
    parser.add_argument("--output", type=Path, default=Path("logs/replay.jsonl"))
    parser.add_argument("--backend", choices=["mock", "qwen"], default="mock")
    parser.add_argument("--cash", type=Decimal, default=Decimal("1000000"))
    args = parser.parse_args()
    backend = MockBackend()
    if args.backend == "qwen":
        endpoint, model = os.getenv("QWEN_ENDPOINT"), os.getenv("QWEN_MODEL", "Qwen/Qwen3-8B")
        if not endpoint or not model:
            parser.error("qwen requires QWEN_ENDPOINT and a nonempty model name")
        backend = QwenBackend(endpoint, model, os.getenv("QWEN_API_KEY"), context_events=args.context_events)
    if args.context_events < 1:
        parser.error("context-events must be positive")
    if args.source == "csv":
        if not args.csv:
            parser.error("csv source requires an input path")
        source = CSVReplay(args.csv)
    else:
        if args.csv or not args.symbols or not os.getenv("KABU_API_TOKEN"):
            parser.error("kabu requires --symbols and KABU_API_TOKEN, without a CSV path")
        source = KabuBoardSource(tuple(args.symbols.split(",")), os.environ["KABU_API_TOKEN"],
                                 os.getenv("KABU_BASE_URL", "http://127.0.0.1:18080/kabusapi"),
                                 args.polls, args.interval)
    if args.csv and args.csv.resolve() == args.output.resolve():
        parser.error("output must differ from input")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects prior runs and raw input from accidental overwrite.
    with args.output.open("x", encoding="utf-8") as output:
        summary = run(source, backend, output, PaperBroker(args.cash), args.context_events)
    print(json.dumps(summary, default=json_default, ensure_ascii=False))


if __name__ == "__main__":
    main()

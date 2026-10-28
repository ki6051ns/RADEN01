import argparse
import json
import os
from decimal import Decimal
from pathlib import Path

from .core import CSVReplay, MockBackend, PaperBroker, json_default, run
from .qwen import QwenBackend


def main():
    parser = argparse.ArgumentParser(description="RADEN01 paper-only CSV replay")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--output", type=Path, default=Path("logs/replay.jsonl"))
    parser.add_argument("--backend", choices=["mock", "qwen"], default="mock")
    parser.add_argument("--cash", type=Decimal, default=Decimal("1000000"))
    args = parser.parse_args()
    backend = MockBackend()
    if args.backend == "qwen":
        endpoint, model = os.getenv("QWEN_ENDPOINT"), os.getenv("QWEN_MODEL")
        if not endpoint or not model:
            parser.error("qwen requires QWEN_ENDPOINT and QWEN_MODEL")
        backend = QwenBackend(endpoint, model, os.getenv("QWEN_API_KEY"))
    if args.csv.resolve() == args.output.resolve():
        parser.error("output must differ from input")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects prior runs and raw input from accidental overwrite.
    with args.output.open("x", encoding="utf-8") as output:
        summary = run(CSVReplay(args.csv), backend, output, PaperBroker(args.cash))
    print(json.dumps(summary, default=json_default, ensure_ascii=False))


if __name__ == "__main__":
    main()

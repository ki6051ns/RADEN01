"""Summarize decision telemetry without loading or exporting raw market records."""
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path


def summarize(path):
    groups = defaultdict(list)
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            if record.get("type") == "decision":
                groups[(record["model"], record["agent"])].append(record)
    results = []
    for (model, agent), records in sorted(groups.items()):
        latencies = sorted(r["latency_ms"] for r in records)
        measured = [r["input_tokens"] for r in records if r["input_tokens"] is not None]
        results.append({"model": model, "agent": agent, "calls": len(records),
                        "errors": sum(r["status"] != "ok" for r in records),
                        "p50_ms": latencies[math.ceil(len(latencies) * .5) - 1],
                        "p95_ms": latencies[math.ceil(len(latencies) * .95) - 1],
                        "input_tokens_total": sum(measured), "token_calls": len(measured),
                        "token_sources": sorted(set(r["token_source"] for r in records)),
                        "actions": {a: sum(r["action"] == a for r in records) for a in ("buy", "sell", "hold")}})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.log), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

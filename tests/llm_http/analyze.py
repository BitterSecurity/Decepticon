"""Validate the measurements and export transparent distributions and raw evidence."""

import argparse
import gzip
import hashlib
import json
import math
import platform
import statistics
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def distribution(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "min": ordered[0],
        "median": statistics.median(ordered),
        "p95": ordered[math.ceil(len(ordered) * 0.95) - 1],
        "p99": ordered[math.ceil(len(ordered) * 0.99) - 1],
        "max": ordered[-1],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--diagnostics-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    observed = json.loads((args.results_dir / "evidence.json").read_text())
    if not observed["qualified"] or len(observed["replay"]) != 18 or len(observed["live"]) != 12:
        raise AssertionError("incomplete or failed primary experiment")
    for filename, digest in observed["sources"].items():
        if hashlib.sha256((HERE / filename).read_bytes()).hexdigest() != digest:
            raise AssertionError(f"current source differs from measurement: {filename}")
    binary = HERE / "rust/target/release/llm-http-comparison"
    if hashlib.sha256(binary.read_bytes()).hexdigest() != observed["binary_sha256"]:
        raise AssertionError("current binary differs from measurement")
    if len(observed["failures"]) != 6:
        raise AssertionError("missing HTTP rejection/truncated-stream checks")
    if {(f["name"], f["kind"]) for f in observed["failures"]} != {
        (name, kind)
        for name in ("langchain", "python", "native")
        for kind in ("reject", "truncated")
    }:
        raise AssertionError("negative coverage differs")
    if not all(row["call"]["qualified"] for row in observed["live"]):
        raise AssertionError("failed direct live call")
    scenario = json.loads((HERE / "scenario.json").read_text())
    successful_calls = [row["call"] for row in observed["live"]] + [
        call for row in observed["replay"] for call in row["calls"]
    ]
    for call in successful_calls:
        if (
            not call["completed"]
            or call["error"] is not None
            or call["text"] != scenario["expected"]
            or "".join(call["deltas"]) != scenario["expected"]
            or call["model"] != scenario["body"]["model"]
            or call["usage"] != scenario["recorded_usage"]
        ):
            raise AssertionError("observed text, model or token usage differs")
    for failure in observed["failures"]:
        if (
            failure["call"]["completed"]
            or failure["metrics"]["attempts"] != 1
            or failure["metrics"]["failures"] != 1
        ):
            raise AssertionError("invalid response accepted or retried")
    summary: dict[str, Any] = {"replay": {}, "live": {}, "baseline": {}}
    for concurrency in (1, 4):
        summary["baseline"][str(concurrency)] = distribution(
            [
                100_000 / r["wall_ms"]
                for r in observed["baseline"]
                if r["concurrency"] == concurrency
            ]
        )
    for name in ("langchain", "python", "native"):
        summary["replay"][name] = {}
        for concurrency in (1, 4):
            rows = [
                r
                for r in observed["replay"]
                if r["name"] == name and r["concurrency"] == concurrency
            ]
            if len(rows) != 3:
                raise AssertionError("unbalanced repeated measurements")
            calls = []
            for row in rows:
                if len(row["calls"]) != 100 or not all(c["qualified"] for c in row["calls"]):
                    raise AssertionError("failed or missing calls")
                if row["after"]["attempts"] - row["before"]["attempts"] != 100:
                    raise AssertionError("wire attempts differ from completed request count")
                if row["after"]["completed"] - row["before"]["completed"] != 100:
                    raise AssertionError("completion count differs")
                if row["after"]["os"]["affinity"] != str(observed["affinity"]["gateways"]):
                    raise AssertionError("gateway affinity differs")
                if (
                    row["fixture_after"]["attempts"] - row["fixture_before"]["attempts"] != 100
                    or row["fixture_after"]["completed"] - row["fixture_before"]["completed"] != 100
                ):
                    raise AssertionError("upstream did not receive every equal request")
                calls.extend(row["calls"])
            gaps = [
                b - a for c in calls for a, b in zip(c["delta_times_ms"], c["delta_times_ms"][1:])
            ]
            summary["replay"][name][str(concurrency)] = {
                "calls": len(calls),
                "success_rate": 1.0,
                "rps": distribution([100_000 / r["wall_ms"] for r in rows]),
                "cpu_ms_per_request": distribution(
                    [
                        (r["after"]["resources"]["cpu_ns"] - r["before"]["resources"]["cpu_ns"])
                        / 1e8
                        for r in rows
                    ]
                ),
                "peak_rss_mib": max(r["after"]["resources"]["peak_rss_kib"] for r in rows) / 1024,
                "pss_mib": distribution([r["after"]["os"]["pss_kib"] / 1024 for r in rows]),
                "threads": max(r["after"]["resources"]["threads"] for r in rows),
                "fds": max(r["after"]["resources"]["fds"] for r in rows),
                "fixture_new_peers": [
                    r["fixture_after"]["peer_connections"] - r["fixture_before"]["peer_connections"]
                    for r in rows
                ],
                "latency_ms": {
                    k: distribution([c[k] for c in calls])
                    for k in ("ttfb_ms", "ttft_ms", "wall_ms", "terminal_ms")
                },
                "inter_chunk_gap_ms": distribution(gaps),
                "sse_payload_bytes": distribution([c["sse_payload_bytes"] for c in calls]),
            }
        live = [r for r in observed["live"] if r["name"] == name and r["phase"] == "warm"]
        if len(live) != 3 or not all(r["call"]["qualified"] for r in live):
            raise AssertionError("missing qualified live requests")
        provider_sockets = []
        for row in live:
            sockets = [
                s
                for s in row["after"]["os"]["tcp"]
                if s["remote_port"] == 443 and s["state"] == "01"
            ]
            if len(sockets) != 1:
                raise AssertionError("unexpected provider connection count")
            provider_sockets.append(sockets[0]["inode"])
        if len(set(provider_sockets)) != 1:
            raise AssertionError("provider TCP connection not reused")
        gaps = [
            b - a
            for r in live
            for a, b in zip(r["call"]["delta_times_ms"], r["call"]["delta_times_ms"][1:])
        ]
        summary["live"][name] = {
            "warm_calls": 3,
            "first_call": next(
                r["call"] for r in observed["live"] if r["name"] == name and r["phase"] == "first"
            ),
            "latency_ms": {
                k: distribution([r["call"][k] for r in live])
                for k in ("ttfb_ms", "ttft_ms", "wall_ms", "terminal_ms")
            },
            "cpu_ms_per_request": distribution(
                [
                    (r["after"]["resources"]["cpu_ns"] - r["before"]["resources"]["cpu_ns"]) / 1e6
                    for r in live
                ]
            ),
            "peak_rss_mib": max(r["after"]["resources"]["peak_rss_kib"] for r in live) / 1024,
            "inter_chunk_gap_ms": distribution(gaps),
            "usage": [r["call"]["usage"] for r in live],
            "provider_connection_reused": True,
        }
    diagnostics = {}
    for name in ("final-02", "final-03", "final-04", "final-05", "final-06"):
        path = args.diagnostics_dir / name / "evidence.json"
        if path.exists():
            diagnostics[name] = json.loads(path.read_text())
    preflight = args.diagnostics_dir / "preflight-one/functional.json"
    if preflight.exists():
        diagnostics["sdk-functional-discovery"] = json.loads(preflight.read_text())
    archive = {
        "host": platform.platform(),
        "python": sys.version,
        "analysis_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "primary": observed,
        "summary": summary,
        "diagnostics": diagnostics,
        "diagnostic_notes": {
            "final-02": "preliminary full cohort before TCP_NODELAY alignment",
            "final-03": "interrupted duplicate after a setup patch failed to apply; not performance evidence",
            "final-04": "TCP_NODELAY aligned, before added timing and encoding observations",
            "final-05": "qualified earlier cohort before configurable pools and paced fixture; retained, not primary",
            "final-06": "qualified cohort before test-local lint configuration correction; retained, not primary",
            "sdk-functional-discovery": "optional SDK failed completion qualification; removed at user direction; no performance inference",
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (args.output_dir / "evidence.json.gz").write_bytes(
        gzip.compress(json.dumps(archive).encode(), mtime=0)
    )
    print(
        json.dumps(
            {
                "qualified_replay_calls": 1800,
                "qualified_live_calls": 12,
                "expected_rejections": 6,
                "diagnostic_runs": list(diagnostics),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

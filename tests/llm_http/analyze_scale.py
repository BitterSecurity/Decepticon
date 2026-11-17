"""Export the complete, budget-verified concurrency ramp, including failed cells."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from analyze import HERE, distribution

NAMES = ("langchain", "python", "native")
POINTS = {1: 8, 4: 16, 16: 32, 64: 128, 128: 256, 256: 512, 512: 1024}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--diagnostics-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    observed = json.loads((args.results_dir / "evidence.json").read_text())
    if not observed["complete"] or len(observed["rows"]) != 63:
        raise AssertionError("incomplete resource ramp")
    expected_cells = {(trial, name, c) for trial in range(3) for name in NAMES for c in POINTS}
    cells = [(r["trial"], r["name"], r["concurrency"]) for r in observed["rows"]]
    if len(set(cells)) != 63 or set(cells) != expected_cells:
        raise AssertionError("missing or duplicate ramp cells")
    for filename, digest in observed["sources"].items():
        if hashlib.sha256((HERE / filename).read_bytes()).hexdigest() != digest:
            raise AssertionError(f"measured source changed: {filename}")
    if (
        hashlib.sha256((HERE / "rust/target/release/llm-http-comparison").read_bytes()).hexdigest()
        != observed["binary_sha256"]
    ):
        raise AssertionError("measured binary changed")
    if len(observed["startup"]) != 9:
        raise AssertionError("missing fresh budgeted services")
    if len({s["ready"]["pid"] for s in observed["startup"]}) != 9 or {
        (s["trial"], s["name"]) for s in observed["startup"]
    } != {(trial, name) for trial in range(3) for name in NAMES}:
        raise AssertionError("repeated services are not distinct and balanced")
    for startup in observed["startup"]:
        budget = startup["initial_budget"]
        if (
            budget["cpu.max"] != "100000 100000"
            or budget["memory.max"] != "268435456"
            or budget["memory.swap.max"] != "0"
        ):
            raise AssertionError("unequal kernel budgets")
        if startup["ready"]["fd_limits"] != [8192, 8192] or startup["ready"]["os"][
            "affinity"
        ] != str(observed["affinity"]["gateways"]):
            raise AssertionError("unequal FD limits or CPU affinity")
    if len(observed["driver_baseline"]) != len(POINTS):
        raise AssertionError("missing driver ceiling checks")
    for baseline in observed["driver_baseline"]:
        if len(baseline["calls"]) != POINTS[baseline["concurrency"]] or not all(
            c["qualified"] for c in baseline["calls"]
        ):
            raise AssertionError("driver baseline failed")
    expected_text = json.loads((HERE / "scenario.json").read_text())["expected"]
    summary: dict[str, Any] = {}
    for name in NAMES:
        points = {}
        for concurrency, count in POINTS.items():
            rows = [
                r for r in observed["rows"] if r["name"] == name and r["concurrency"] == concurrency
            ]
            trials = []
            for row in rows:
                if row["count"] != count:
                    raise AssertionError("request count differs")
                if row.get("unavailable"):
                    trials.append({"trial": row["trial"], "available": False, "slo_pass": False})
                    continue
                calls = row["calls"]
                if len(calls) != count:
                    raise AssertionError("missing outcomes")
                if row["before_os"]["affinity"] != str(observed["affinity"]["gateways"]):
                    raise AssertionError("gateway affinity differs")
                success = sum(c["qualified"] for c in calls)
                if any(
                    c["qualified"]
                    and (
                        c["text"] != expected_text
                        or c["model"] != "gpt-6.1-sol"
                        or not c["completed"]
                        or c["error"] is not None
                    )
                    for c in calls
                ):
                    raise AssertionError("qualified outcome lacks expected observed output")
                latency = distribution([c["wall_ms"] for c in calls])
                before, after = row["before"], row["after"]
                samples = [s for s in [before, *row["samples"], after] if not s.get("removed")]
                cpu_us = (
                    after["cpu.stat"]["usage_usec"] - before["cpu.stat"]["usage_usec"]
                    if not after.get("removed")
                    else None
                )
                fixture_cpu_ms = (
                    row["fixture_after"]["resources"]["cpu_ns"]
                    - row["fixture_before"]["resources"]["cpu_ns"]
                ) / 1e6
                for fixture_metrics in (row["fixture_before"], row["fixture_after"]):
                    if fixture_metrics["os"]["affinity"] != str(observed["affinity"]["fixture"]):
                        raise AssertionError("fixture affinity differs")
                if row["fixture_after"]["attempts"] - row["fixture_before"]["attempts"] > count:
                    raise AssertionError("unexpected additional upstream attempts")
                if success == count and any(
                    row["fixture_after"][key] - row["fixture_before"][key] != expected
                    for key, expected in (
                        ("attempts", count),
                        ("completed", count),
                        ("failures", 0),
                    )
                ):
                    raise AssertionError("successful calls lack equal validated upstream requests")
                for snapshot in samples:
                    if (
                        snapshot["cpu.max"] != "100000 100000"
                        or snapshot["memory.max"] != "268435456"
                        or snapshot["memory.swap.max"] != "0"
                    ):
                        raise AssertionError("runtime resource budgets changed")
                trials.append(
                    {
                        "trial": row["trial"],
                        "available": True,
                        "successes": success,
                        "count": count,
                        "success_rate": success / count,
                        "good_rps": success * 1000 / row["wall_ms"],
                        "driver_cpu_fraction": row["driver_cpu_ms"] / row["wall_ms"],
                        "fixture_cpu_fraction": fixture_cpu_ms / row["wall_ms"],
                        "latency_ms": latency,
                        "cpu_ms_per_attempt": cpu_us / 1000 / count if cpu_us is not None else None,
                        "gateway_cpu_fraction": cpu_us / 1000 / row["wall_ms"]
                        if cpu_us is not None
                        else None,
                        "max_sampled_memory_mib": max(int(s["memory.current"]) for s in samples)
                        / 2**20,
                        "service_lifetime_peak_mib": max(int(s["memory.peak"]) for s in samples)
                        / 2**20,
                        "max_sampled_socket_mib": max(s["memory.stat"]["sock"] for s in samples)
                        / 2**20,
                        "oom_kills": max(s["memory.events"]["oom_kill"] for s in samples),
                        "throttled_ms": (
                            after["cpu.stat"]["throttled_usec"]
                            - before["cpu.stat"]["throttled_usec"]
                        )
                        / 1000
                        if not after.get("removed")
                        else None,
                        "slo_pass": success / count >= observed["slo"]["success_rate"]
                        and latency["p95"] <= observed["slo"]["p95_wall_ms"],
                    }
                )
            points[str(concurrency)] = {
                "trials": trials,
                "all_trials_pass_slo": all(t["slo_pass"] for t in trials),
            }
        passing = [int(c) for c, point in points.items() if point["all_trials_pass_slo"]]
        summary[name] = {
            "points": points,
            "largest_tested_all_trials_passing_concurrency": max(passing) if passing else None,
        }
    diagnostics = {}
    for name in ("scale-01", "scale-02", "scale-03", "scale-04"):
        path = args.diagnostics_dir / name / "evidence.json"
        if path.exists():
            diagnostics[name] = json.loads(path.read_text())
    discovery = args.diagnostics_dir / "fd-limit-discovery.json"
    archive = {
        "analysis_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "primary": observed,
        "summary": summary,
        "diagnostics": diagnostics,
        "fd_discovery": json.loads(discovery.read_text()) if discovery.exists() else None,
        "driver_discovery": json.loads(
            (args.diagnostics_dir / "scale-04/driver-discovery.json").read_text()
        )
        if (args.diagnostics_dir / "scale-04/driver-discovery.json").exists()
        else None,
        "diagnostic_notes": {
            "scale-01": "interrupted preliminary setup before final instrumentation",
            "scale-02": "interrupted preliminary ramp with systemd default FD limit 1024; excluded from final curve",
            "scale-03": "obsolete queued launch removed before execution for lint correction",
            "scale-04": "interrupted HTTPX pooled-driver ramp after common high-concurrency slowdown; replaced load driver and added baseline/driver/fixture CPU measurements",
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "scale-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (args.output_dir / "scale-evidence.json.gz").write_bytes(
        gzip.compress(json.dumps(archive).encode(), mtime=0)
    )
    print(
        json.dumps(
            {
                "complete_cells": 63,
                "capacity": {
                    n: s["largest_tested_all_trials_passing_concurrency"]
                    for n, s in summary.items()
                },
            }
        )
    )


if __name__ == "__main__":
    main()

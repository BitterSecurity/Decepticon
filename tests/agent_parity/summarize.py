"""Retain complete traces and compare every checkpoint in the language control pair."""

import argparse
import gzip
import hashlib
import json
import sqlite3
import statistics
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def metrics(case: dict[str, Any]) -> dict[str, float]:
    phases = case["phases"]
    return {
        "wall_ms": sum(p["wall_ms"] for p in phases),
        "agent_cpu_ms": sum(
            p["import_cpu_ms"] + p["setup_cpu_ms"] + p["active_cpu_ms"] for p in phases
        ),
        "import_cpu_ms": sum(p["import_cpu_ms"] for p in phases),
        "setup_cpu_ms": sum(p["setup_cpu_ms"] for p in phases),
        "active_cpu_ms": sum(p["active_cpu_ms"] for p in phases),
        "local_tree_cpu_ms": sum(
            p["local_process_tree_user_cpu_ms"] + p["local_process_tree_system_cpu_ms"]
            for p in phases
        ),
        "peak_rss_mib": max(p["resources"]["peak_rss_kib"] for p in phases) / 1024,
        "sampled_threads": max(p["sampled_peaks_10ms"]["threads"] for p in phases),
        "sampled_fds": max(p["sampled_peaks_10ms"]["fds"] for p in phases),
        "last_sample_write_bytes": sum(
            p["last_sample_counters"].get("write_bytes", 0) for p in phases
        ),
        "checkpoint_bytes": case["checkpoint_bytes"],
        "model_calls": len(case["model_calls"]),
        "event_count": len(case["events"]),
        "event_log_bytes": case["event_log_bytes"],
        "relay_elapsed_ms": sum(c["elapsed_ms"] for c in case["model_calls"]),
    }


def history(root: Path, label: str) -> list[list[Any]]:
    with sqlite3.connect(root / label / "checkpoint.sqlite") as connection:
        return [
            [role, json.loads(state), json.loads(todos), done]
            for role, state, todos, done in connection.execute(
                "SELECT role,state,todos,done FROM history ORDER BY id"
            )
        ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--diagnostics-dir", type=Path, required=True)
    parser.add_argument("--paired-results-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.results_dir.resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    if len(manifest["trials"]) != 40 or not all(t["qualified"] for t in manifest["trials"]):
        raise AssertionError("suite is incomplete or contains failed trials")
    paired_root = args.paired_results_dir.resolve()
    paired_manifest = json.loads((paired_root / "manifest.json").read_text())
    if len(paired_manifest["trials"]) != 10 or not all(
        t["qualified"] for t in paired_manifest["trials"]
    ):
        raise AssertionError("affinity-paired trials incomplete or failed")
    if len(paired_manifest["affinity"]) != 1:
        raise AssertionError("language pair was not pinned to one CPU")
    cases = {}
    source_hashes = None
    for trial in [*manifest["trials"], *paired_manifest["trials"]]:
        label = trial["label"]
        trial_root = paired_root if label.startswith("paired-") else root
        case = json.loads((trial_root / (label + ".json")).read_text())
        case["experiment"] = "paired-replay" if label.startswith("paired-") else case["mode"]
        if not case["qualified"] or any(p["exit"] != p["expected_exit"] for p in case["phases"]):
            raise AssertionError("qualification or crash/restart failed")
        if source_hashes is None:
            source_hashes = case["source_sha256"]
        if source_hashes != case["source_sha256"]:
            raise AssertionError("measurement source changed during suite")
        starts = [e for e in case["events"] if e["event"] == "process.start"]
        if len(starts) != 2 or any(e["sqlite_version"] != "3.45.1" for e in starts):
            raise AssertionError("SQLite implementations differ")
        sandbox = next(e for e in case["events"] if e["event"] == "sandbox.finished")
        configured = sandbox["host_config"]
        if not (
            sandbox["image"] == case["image"]
            and sandbox["user"] == "65534:65534"
            and configured["NetworkMode"] == "none"
            and configured["ReadonlyRootfs"]
            and configured["CapDrop"] == ["ALL"]
            and configured["SecurityOpt"] == ["no-new-privileges"]
            and configured["Memory"] == 128 * 1024 * 1024
            and configured["NanoCpus"] == 1_000_000_000
            and configured["PidsLimit"] == 32
        ):
            raise AssertionError("actual sandbox options differ from the shared profile")
        case["event_log_bytes"] = (trial_root / label / "events.jsonl").stat().st_size
        cases[label] = case
    profile = json.loads((HERE / "profile.json").read_text())
    golden = cases["live-0-native"]["model_calls"]
    for case in cases.values():
        for role in ("parent", "inspector"):
            first = next(c["upstream_request"] for c in case["model_calls"] if c["role"] == role)
            baseline = next(c["upstream_request"] for c in golden if c["role"] == role)
            if first != baseline or first["instructions"] != profile[role]["system"]:
                raise AssertionError("effective first prompt or schema differs")
        if case["mode"] == "replay":
            trajectory = [
                (c["phase"], c["role"], c["upstream_request"]) for c in case["model_calls"]
            ]
            expected = [(c["phase"], c["role"], c["upstream_request"]) for c in golden]
            if trajectory != expected:
                raise AssertionError("replay changed the live trajectory")
    histories, proofs = {}, []
    for trial_root, prefix in ((root, "replay"), (paired_root, "paired")):
        for trial in range(5):
            native = history(trial_root, f"{prefix}-{trial}-native")
            python = history(trial_root, f"{prefix}-{trial}-python-native")
            if native != python:
                raise AssertionError("language pair checkpoint histories differ")
            histories[f"{prefix}-{trial}"] = {"native": native, "python-native": python}
            proofs.append(
                {
                    "experiment": prefix,
                    "trial": trial,
                    "rows": len(native),
                    "identical": True,
                    "sha256": digest(native),
                }
            )
    grouped: dict[str, dict[str, list[dict[str, float]]]] = {}
    for case in cases.values():
        grouped.setdefault(case["experiment"], {}).setdefault(case["framework"], []).append(
            metrics(case)
        )
    paired_ratios = []
    for trial in range(5):
        native = metrics(cases[f"paired-{trial}-native"])
        python = metrics(cases[f"paired-{trial}-python-native"])
        paired_ratios.append(
            {
                "trial": trial,
                **{
                    key: python[key] / native[key]
                    for key in ("agent_cpu_ms", "active_cpu_ms", "peak_rss_mib")
                },
            }
        )
    summary = {
        mode: {
            framework: {
                metric: {
                    "median": statistics.median(row[metric] for row in rows),
                    "min": min(row[metric] for row in rows),
                    "max": max(row[metric] for row in rows),
                }
                for metric in rows[0]
            }
            for framework, rows in candidates.items()
        }
        for mode, candidates in grouped.items()
    }
    failures = {}
    failure_stderr = {}
    diagnostics = [
        *args.diagnostics_dir.glob("*.json"),
        *args.diagnostics_dir.glob("reference-*/evidence.json"),
    ]
    for path in sorted(diagnostics):
        diagnostic = json.loads(path.read_text())
        if diagnostic.get("qualified") is False:
            label = str(path.relative_to(args.diagnostics_dir)).removesuffix(".json")
            failures[label] = diagnostic
            workspace = path.parent if path.name == "evidence.json" else path.with_suffix("")
            failure_stderr[label] = {
                phase: (workspace / (phase + ".stderr")).read_text()
                for phase in ("prepare", "resume")
                if (workspace / (phase + ".stderr")).exists()
            }
    if not all(f"negative-{name}" in failures for name in grouped["live"]):
        raise AssertionError("missing deliberate mismatch rejection evidence")
    archive = {
        "manifest": manifest,
        "paired_manifest": paired_manifest,
        "cases": cases,
        "summary": summary,
        "paired_ratios": paired_ratios,
        "checkpoint_proofs": proofs,
        "matched_checkpoint_histories": histories,
        "diagnostic_failures": failures,
        "diagnostic_stderr": failure_stderr,
        "provider_sha256": hashlib.sha256(
            (HERE.parents[1] / "config/codex_chatgpt_handler.py").read_bytes()
        ).hexdigest(),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "evidence.json.gz").write_bytes(
        gzip.compress(json.dumps(archive, ensure_ascii=False).encode(), mtime=0)
    )
    (args.output_dir / "summary.json").write_text(
        json.dumps(
            {"summary": summary, "paired_ratios": paired_ratios, "checkpoint_proofs": proofs},
            indent=2,
        )
        + "\n"
    )
    print(
        json.dumps(
            {
                "qualified_trials": len(cases),
                "failures_retained": list(failures),
                "checkpoint_proofs": proofs,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

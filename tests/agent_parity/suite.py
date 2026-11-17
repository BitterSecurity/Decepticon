"""Run cold-process live trials and a balanced replay of one qualified live trace."""

import argparse
import json
import os
import platform
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CANDIDATES = ["native", "python-native", "rig", "langchain", "deepagents"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    manifest = {
        "host": platform.platform(),
        "python": sys.version,
        "sqlite": sqlite3.sqlite_version,
        "affinity": sorted(os.sched_getaffinity(0)),
        "trials": [],
    }
    golden = root / "live-0-native.json"
    for mode, rounds in (("live", 3), ("replay", 5)):
        for trial in range(rounds):
            order = CANDIDATES[trial:] + CANDIDATES[:trial]
            for framework in order:
                label = f"{mode}-{trial}-{framework}"
                output = root / (label + ".json")
                command = [
                    sys.executable,
                    str(HERE / "run.py"),
                    "--framework",
                    framework,
                    "--work-dir",
                    str(root / label),
                    "--output",
                    str(output),
                ]
                if framework in {"native", "rig"}:
                    command += ["--binary", str(HERE / "rust/target" / framework)]
                if mode == "replay":
                    command += ["--replay", str(golden)]
                with (root / (label + ".log")).open("w") as log:
                    process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
                qualified = output.exists() and json.loads(output.read_text())["qualified"]
                manifest["trials"].append(
                    {
                        "label": label,
                        "exit": process.returncode,
                        "qualified": qualified,
                    }
                )
                (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
                print(json.dumps(manifest["trials"][-1]), flush=True)
                if label == "live-0-native" and not qualified:
                    raise RuntimeError("live reference did not qualify; no replay possible")
    if not all(t["qualified"] for t in manifest["trials"]):
        raise RuntimeError("some trials failed qualification; retain them in the report")


if __name__ == "__main__":
    main()

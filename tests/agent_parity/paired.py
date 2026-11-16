"""Alternate the language control pair on one CPU with one warm measurement relay."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import run

HERE = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    cpu = max(os.sched_getaffinity(0))
    os.sched_setaffinity(0, {cpu})
    manifest = {
        "affinity": sorted(os.sched_getaffinity(0)),
        "relay": "one warm common relay process; a fresh server for each case",
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "trials": [],
    }
    for trial in range(5):
        order = ["native", "python-native"] if trial % 2 == 0 else ["python-native", "native"]
        for framework in order:
            label = f"paired-{trial}-{framework}"
            output = root / (label + ".json")
            run.CALLS.clear()
            run.REPLAY.clear()
            run.CURSORS.clear()
            sys.argv = [
                str(HERE / "run.py"),
                "--framework",
                framework,
                "--replay",
                str(args.golden.resolve()),
                "--work-dir",
                str(root / label),
                "--output",
                str(output),
            ]
            if framework == "native":
                sys.argv += ["--binary", str(HERE / "rust/target/native")]
            try:
                run.main()
            finally:
                qualified = output.exists() and json.loads(output.read_text())["qualified"]
                manifest["trials"].append({"label": label, "qualified": qualified})
                (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            if not qualified:
                raise AssertionError("language pair did not qualify")


if __name__ == "__main__":
    main()

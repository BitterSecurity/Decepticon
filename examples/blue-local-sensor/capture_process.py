import json
import os
import signal
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    log_path = Path(sys.argv[1])
    command = sys.argv[2:]
    if not command:
        raise SystemExit("usage: capture_process.py LOG_PATH COMMAND [ARGS...]")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    child = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        bufsize=1,
        start_new_session=True,
    )
    lock = threading.Lock()

    def forward(signum, _frame) -> None:
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)

    def drain(stream, name: str) -> None:
        for line in stream:
            event = {
                "event_id": str(uuid.uuid4()),
                "event_type": "process_log",
                "source": "target-process",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "stream": name,
                "message": line.rstrip("\r\n"),
            }
            with lock:
                log.write(json.dumps(event, separators=(",", ":")) + "\n")

    with log_path.open("a", encoding="utf-8", buffering=1) as log:
        threads = [
            threading.Thread(target=drain, args=(child.stdout, "stdout")),
            threading.Thread(target=drain, args=(child.stderr, "stderr")),
        ]
        for thread in threads:
            thread.start()
        status = child.wait()
        for thread in threads:
            thread.join()
    return status


if __name__ == "__main__":
    raise SystemExit(main())

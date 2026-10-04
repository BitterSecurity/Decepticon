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
    capture_run_id = str(uuid.uuid4())

    def forward(signum, _frame) -> None:
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)

    with log_path.open("a", encoding="utf-8", buffering=1) as log:

        def emit(event_type: str, message: str, **fields: object) -> None:
            event = {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "source": "target-process",
                "capture_run_id": capture_run_id,
                "pid": child.pid,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "message": message,
                **fields,
            }
            with lock:
                log.write(json.dumps(event, separators=(",", ":")) + "\n")

        def drain(stream, name: str) -> None:
            for line in stream:
                emit("process_log", line.rstrip("\r\n"), stream=name)

        emit("process_lifecycle", "process started")
        threads = [
            threading.Thread(target=drain, args=(child.stdout, "stdout")),
            threading.Thread(target=drain, args=(child.stderr, "stderr")),
        ]
        for thread in threads:
            thread.start()
        status = child.wait()
        for thread in threads:
            thread.join()
        emit("process_lifecycle", "process exited", exit_code=status)
    return status


if __name__ == "__main__":
    raise SystemExit(main())

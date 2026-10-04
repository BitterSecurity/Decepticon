import importlib.util
import json
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from decepticon.tools.defense.blue_sensor import blue_sensor_search, blue_sensor_timeline

RECEIVER_PATH = (
    Path(__file__).resolve().parents[5] / "examples" / "blue-local-sensor" / "blue_receiver.py"
)
CAPTURE_PATH = RECEIVER_PATH.with_name("capture_process.py")


@pytest.fixture
def receiver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BLUE_EVENT_DB", str(tmp_path / "observations.sqlite3"))
    monkeypatch.setenv("BLUE_BODY_DIR", str(tmp_path / "bodies"))
    monkeypatch.setenv("BLUE_TARGET_ID", "service-a")
    spec = importlib.util.spec_from_file_location("test_blue_receiver", RECEIVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    module.STORE.db.close()


def test_structured_target_log_keeps_raw_and_correlation(receiver) -> None:
    original = json.dumps(
        {
            "@timestamp": "2026-10-04T12:30:00+09:00",
            "level": "ERROR",
            "message": "database unavailable",
            "traceId": "trace-42",
            "requestId": "request-7",
            "status": 503,
            "source": "untrusted-app-field",
        }
    )
    event = receiver.normalize(
        {
            "sensor_file": "/target-logs/app.jsonl",
            "sensor_offset": 150,
            "sensor_read_at": "2026-10-04T03:30:01Z",
            "log": original,
        }
    )
    assert event["schema_version"] == 1
    assert event["target_id"] == "service-a"
    assert event["signal_type"] == "log"
    assert event["event_type"] == "application_log"
    assert event["source"] == "target-log-file"
    assert event["occurred_at"] == "2026-10-04T03:30:00+00:00"
    assert event["severity_text"] == "ERROR"
    assert event["trace_id"] == "trace-42"
    assert event["request_id"] == "request-7"
    assert event["raw_record"] == original
    assert event["attributes"]["status"] == 503


@pytest.mark.parametrize(
    "timestamp", [1791084600, 1791084600000, 1791084600000000, 1791084600000000000]
)
def test_structured_target_log_accepts_epoch_precision(receiver, timestamp: int) -> None:
    event = receiver.normalize(
        {
            "sensor_file": "/target-logs/app.jsonl",
            "sensor_offset": 150,
            "sensor_read_at": "2026-10-04T03:30:01Z",
            "log": json.dumps({"ts": timestamp, "message": "observed"}),
        }
    )
    assert event["occurred_at"] == "2026-10-04T03:30:00+00:00"


def test_proxy_log_prefix_can_follow_a_local_collector_path(receiver, monkeypatch) -> None:
    monkeypatch.setattr(receiver, "PROXY_LOG_PREFIX", "/tmp/blue-logs/access")
    event = receiver.normalize(
        {
            "sensor_file": "/tmp/blue-logs/access.jsonl",
            "sensor_offset": 10,
            "sensor_read_at": "2026-10-04T03:30:01Z",
            "blue_request_id": "proxy-2",
            "ts": 1791084600,
            "path": "/read",
            "request": {"method": "GET", "uri": "/read"},
        }
    )
    assert event["event_type"] == "http_access"
    assert event["source"] == "blue-ingress-proxy"


def test_process_wrapper_extracts_embedded_json_without_losing_envelope(receiver) -> None:
    application_line = '{"level":"warn","msg":"rate limit","span_id":"span-1"}'
    original = json.dumps(
        {
            "source": "target-process",
            "target_id": "forged-target",
            "event_id": "wrapper-1",
            "event_type": "process_log",
            "occurred_at": "2026-10-04T03:30:00+00:00",
            "stream": "stderr",
            "message": application_line,
        }
    )
    event = receiver.normalize(
        {
            "sensor_file": "/target-logs/process.jsonl",
            "sensor_offset": 200,
            "sensor_read_at": "2026-10-04T03:30:01Z",
            "log": original,
        }
    )
    assert event["event_type"] == "process_log"
    assert event["event_id"] != "wrapper-1"
    assert event["source_event_id"] == "wrapper-1"
    assert event["source"] == "target-log-file"
    assert event["reported_source"] == "target-process"
    assert event["target_id"] == "service-a"
    assert event["occurred_at"] == "2026-10-04T03:30:00+00:00"
    assert event["raw_record"] == application_line
    assert event["message"] == "rate limit"
    assert event["collector_record"] == original
    assert event["attributes"]["msg"] == "rate limit"
    assert event["span_id"] == "span-1"


def test_owned_process_stdout_and_stderr_become_target_evidence(receiver, tmp_path: Path) -> None:
    log_path = tmp_path / "process.jsonl"
    subprocess.run(
        [
            sys.executable,
            str(CAPTURE_PATH),
            str(log_path),
            sys.executable,
            "-u",
            "-c",
            'import json, sys; print(json.dumps({"request_id": "req-live", "message": "served"})); print("backend warning", file=sys.stderr)',
        ],
        check=True,
        timeout=10,
    )
    lines = log_path.read_text().splitlines()
    assert len(lines) == 4
    events = [
        receiver.normalize(
            {
                "sensor_file": "/target-logs/process.jsonl",
                "sensor_offset": offset,
                "sensor_read_at": "2026-10-04T03:30:01Z",
                "log": line,
            }
        )
        for offset, line in enumerate(lines)
    ]
    receiver.STORE.append(events, [])
    stored = receiver.STORE.page(0, 10)
    assert {event["stream"] for event in stored if event["event_type"] == "process_log"} == {
        "stdout",
        "stderr",
    }
    lifecycle = [event for event in stored if event["event_type"] == "process_lifecycle"]
    assert [event["message"] for event in lifecycle] == ["process started", "process exited"]
    assert lifecycle[1]["exit_code"] == 0
    assert lifecycle[0]["capture_run_id"] == lifecycle[1]["capture_run_id"]
    assert receiver.STORE.search("request_id", "req-live", None, 10)[0]["message"] == "served"


def test_plain_text_log_preserves_repeated_line_after_rotation(receiver) -> None:
    raw = {
        "sensor_file": "/target-logs/app.log",
        "sensor_offset": 31,
        "sensor_read_at": "2026-10-04T03:30:01Z",
        "log": "startup complete",
    }
    event = receiver.normalize(raw)
    retried_chunk = receiver.normalize(raw)
    rotated_file = receiver.normalize({**raw, "sensor_read_at": "2026-10-04T03:31:00Z"})
    assert event["event_type"] == "log_line"
    assert event["message"] == event["raw_record"] == "startup complete"
    assert event["event_id"] == retried_chunk["event_id"]
    assert event["event_id"] != rotated_file["event_id"]


def test_loss_counters_survive_retention_and_receiver_restart(
    receiver, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = [
        receiver.normalize(
            {
                "sensor_file": "/target-logs/service.log",
                "sensor_offset": offset,
                "sensor_read_at": "2026-10-04T03:30:01Z",
                "log": f"record {offset}",
            }
        )
        for offset in (1, 2)
    ]
    receiver.STORE.append(events, [("invalid", "a" * 64), ("invalid", "b" * 64)])
    monkeypatch.setattr(receiver, "EVENT_MAX_ROWS", 1)
    monkeypatch.setattr(receiver, "REJECTED_MAX_ROWS", 1)
    receiver.STORE.cleanup()
    before = receiver.STORE.metrics()
    assert before["events_total"] == 1
    assert before["events_ingested_total"] == 2
    assert before["events_evicted_total"] == 1
    assert before["rejected_total"] == 2
    receiver.STORE.db.close()
    receiver.STORE = receiver.EventStore()
    after = receiver.STORE.metrics()
    assert after["events_ingested_total"] == before["events_ingested_total"]
    assert after["events_evicted_total"] == before["events_evicted_total"]
    assert after["rejected_total"] == before["rejected_total"]
    assert after["counters_started_at"] == before["counters_started_at"]


def test_existing_receiver_database_initializes_durable_counters(receiver) -> None:
    old_event = receiver.normalize(
        {
            "sensor_file": "/target-logs/old.log",
            "sensor_offset": 1,
            "sensor_read_at": "2026-10-04T03:30:01Z",
            "log": "existing record",
        }
    )
    receiver.STORE.append([old_event], [("invalid", "a" * 64)])
    with receiver.STORE.db:
        receiver.STORE.db.execute("DROP TABLE counter_state")
    receiver.STORE.db.close()
    receiver.STORE = receiver.EventStore()
    metrics = receiver.STORE.metrics()
    assert metrics["events_ingested_total"] == 1
    assert metrics["rejected_total"] == 1
    assert metrics["events_evicted_total"] == 0
    assert metrics["counters_started_at"]


def test_collector_metrics_show_explicit_loss_and_paused_inputs(receiver) -> None:
    metrics = receiver.parse_collector_metrics(
        "# HELP fluentbit_input_records_total Ingested records\n"
        'fluentbit_input_records_total{name="blue_proxy"} 7\n'
        'fluentbit_input_records_total{name="blue_target_logs"} 5\n'
        'fluentbit_input_files_opened_total{name="blue_target_logs"} 2\n'
        'fluentbit_input_long_line_skipped_total{name="blue_target_logs"} 2\n'
        'fluentbit_output_dropped_records_total{name="http.0"} 1\n'
        'fluentbit_input_ingestion_paused{name="blue_proxy"} 1\n'
        'fluentbit_input_ingestion_paused{name="blue_target_logs"} 0\n'
        'fluentbit_process_start_time_seconds{hostname="collector"} 1791000000\n'
    )
    assert metrics["collector_input_records_total"] == 12
    assert metrics["collector_long_lines_skipped_total"] == 2
    assert metrics["collector_dropped_records_total"] == 1
    assert metrics["collector_paused_inputs"] == 1
    assert metrics["collector_start_time_seconds"] == 1791000000
    assert metrics["collector_sources"]["proxy"]["records_total"] == 7
    assert metrics["collector_sources"]["proxy"]["paused"] == 1
    assert metrics["collector_sources"]["target_logs"]["records_total"] == 5
    assert metrics["collector_sources"]["target_logs"]["files_opened_total"] == 2


def test_ingest_and_query_serve_normalized_application_log(receiver, monkeypatch) -> None:
    monkeypatch.setattr(
        receiver,
        "collector_metrics",
        lambda: {"collector_available": True, "collector_long_lines_skipped_total": 0},
    )
    ingest = receiver.ThreadingHTTPServer(("127.0.0.1", 0), receiver.IngestHandler)
    query = receiver.ThreadingHTTPServer(("127.0.0.1", 0), receiver.QueryHandler)
    threads = [threading.Thread(target=server.serve_forever) for server in (ingest, query)]
    for thread in threads:
        thread.start()
    try:
        payload = {
            "sensor_file": "/target-logs/service.jsonl",
            "sensor_offset": 55,
            "sensor_read_at": "2026-10-04T03:30:01Z",
            "log": '{"level":"ERROR","message":"connection refused","request_id":"req-9"}',
        }
        request = Request(
            f"http://127.0.0.1:{ingest.server_port}/ingest",
            data=(json.dumps(payload) + "\n").encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request) as response:
            assert json.load(response) == {"accepted": 1, "rejected": 0}
        with urlopen(f"http://127.0.0.1:{query.server_port}/events?after=0&limit=10") as response:
            event = json.load(response)["events"][0]
        assert event["event_type"] == "application_log"
        assert event["request_id"] == "req-9"
        assert event["attributes"]["level"] == "ERROR"
        with urlopen(f"http://127.0.0.1:{query.server_port}/metrics") as response:
            metrics = json.load(response)
        with urlopen(f"http://127.0.0.1:{query.server_port}/sources?limit=10") as response:
            sources = json.load(response)
        assert metrics["events_total"] == 1
        assert metrics["collector_available"] is True
        assert metrics["target_log_events"] == 1
        assert metrics["target_log_last_received_at"] is not None
        assert metrics["proxy_events"] == 0
        assert metrics["proxy_last_received_at"] is None
        assert sources["has_more"] is False
        assert sources["sources"][0]["sensor_file"] == "/target-logs/service.jsonl"
        assert sources["sources"][0]["events_retained"] == 1
    finally:
        for server in (ingest, query):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join()


def test_search_correlates_exact_ids_with_bounded_pagination(
    receiver, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = [
        ("req-1", "trace-1", 10),
        ("req-other", "trace-other", 20),
        ("req-1", "trace-1", 30),
    ]
    events = [
        receiver.normalize(
            {
                "sensor_file": "/target-logs/app.jsonl",
                "sensor_offset": offset,
                "sensor_read_at": "2026-10-04T03:30:01Z",
                "log": json.dumps(
                    {"message": f"at {offset}", "request_id": request_id, "trace_id": trace_id}
                ),
            }
        )
        for request_id, trace_id, offset in records
    ]
    receiver.STORE.append(events, [])
    server = receiver.ThreadingHTTPServer(("127.0.0.1", 0), receiver.QueryHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}/search"
        with urlopen(f"{base}?field=request_id&value=req-1&limit=1") as response:
            first = json.load(response)
        assert [event["seq"] for event in first["events"]] == [3]
        assert first["has_more"] is True
        assert first["next_before"] == 3
        with urlopen(f"{base}?field=request_id&value=req-1&limit=1&before=3") as response:
            second = json.load(response)
        assert [event["seq"] for event in second["events"]] == [1]
        assert second["has_more"] is False
        with urlopen(f"{base}?field=trace_id&value=trace-other") as response:
            trace = json.load(response)
        assert [event["seq"] for event in trace["events"]] == [2]
        monkeypatch.setenv("BLUE_SENSOR_URL", f"http://127.0.0.1:{server.server_port}")
        tool_result = json.loads(
            blue_sensor_search.invoke({"field": "request_id", "value": "req-1"})
        )
        assert [event["seq"] for event in tool_result["events"]] == [3, 1]
        received = datetime.fromisoformat(receiver.STORE.page(0, 1)[0]["collector_received_at"])
        start = received - timedelta(minutes=1)
        end = received + timedelta(minutes=1)
        timeline_query = urlencode(
            {"start_at": start.isoformat(), "end_at": end.isoformat(), "limit": 2}
        )
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/timeline?{timeline_query}"
        ) as response:
            timeline = json.load(response)
        assert [event["seq"] for event in timeline["events"]] == [3, 2]
        assert timeline["next_before"] == 2
        assert timeline["has_more"] is True
        window = json.loads(
            blue_sensor_timeline.invoke(
                {"start_at": start.isoformat(), "end_at": end.isoformat(), "before": 2}
            )
        )
        assert [event["seq"] for event in window["events"]] == [1]
        proxy = receiver.normalize(
            {
                "sensor_file": "/sensor-logs/access.jsonl",
                "sensor_offset": 40,
                "sensor_read_at": receiver.utc_now(),
                "blue_request_id": "proxy-1",
                "ts": received.timestamp(),
                "path": "/",
                "request": {"method": "GET", "uri": "/"},
            }
        )
        receiver.STORE.append([proxy], [])
        proxy_window = json.loads(
            blue_sensor_timeline.invoke(
                {
                    "start_at": start.isoformat(),
                    "end_at": end.isoformat(),
                    "source": "blue-ingress-proxy",
                }
            )
        )
        assert [event["seq"] for event in proxy_window["events"]] == [4]
        with pytest.raises(HTTPError) as error:
            urlopen(f"{base}?field=message&value=at%2010")
        assert error.value.code == 400
        too_wide = urlencode(
            {"start_at": start.isoformat(), "end_at": (start + timedelta(hours=2)).isoformat()}
        )
        with pytest.raises(HTTPError) as error:
            urlopen(f"http://127.0.0.1:{server.server_port}/timeline?{too_wide}")
        assert error.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

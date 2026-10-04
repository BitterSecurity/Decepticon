import json

import httpx
import pytest

from decepticon.tools.defense.blue_sensor import blue_sensor_scan


def test_scan_still_returns_target_events_during_receiver_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_client = httpx.Client

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/metrics":
            return httpx.Response(200, json={"latest_seq": 1})
        if request.url.path == "/events":
            return httpx.Response(200, json={"events": [{"seq": 1, "message": "target log"}]})
        if request.url.path == "/incidents":
            return httpx.Response(200, json={"incidents": []})
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, "Client", lambda **_kwargs: original_client(transport=transport))
    result = json.loads(blue_sensor_scan.invoke({"limit": 10}))
    assert result["events"] == [{"seq": 1, "message": "target log"}]
    assert result["sources"] == []
    assert result["sources_available"] is False

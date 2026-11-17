"""One-worker HTTP gateways using ChatOpenAI or a persistent HTTPX client."""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import uvicorn

HERE = Path(__file__).resolve().parent
SCENARIO = json.loads((HERE / "scenario.json").read_text())
BODY = SCENARIO["body"]
MODE = sys.argv[1]
PORT = int(sys.argv[2])
COUNTS: dict[str, Any] = {"attempts": 0, "completed": 0, "failures": 0}
CLIENT: httpx.AsyncClient
MODEL: Any = None


def resources() -> dict[str, int]:
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())
    return {
        "cpu_ns": time.process_time_ns(),
        "peak_rss_kib": int(status["VmHWM"].split()[0]),
        "rss_kib": int(status["VmRSS"].split()[0]),
        "threads": int(status["Threads"]),
        "fds": len(list(Path("/proc/self/fd").iterdir())),
    }


async def inspect_request(request: httpx.Request) -> None:
    COUNTS["attempts"] += 1
    COUNTS["last_body"] = json.loads(request.content)
    if COUNTS["last_body"] != BODY:
        raise ValueError("outbound request differs from the common profile")
    if request.headers.get("content-type") != "application/json":
        raise ValueError("unexpected or duplicate content type")


async def start() -> None:
    global CLIENT, MODEL
    headers = json.loads(os.environ["BENCH_HEADERS"])
    pool_limit = int(os.environ.get("BENCH_POOL_LIMIT", "4"))
    CLIENT = httpx.AsyncClient(
        headers=headers,
        timeout=120,
        trust_env=False,
        limits=httpx.Limits(
            max_connections=pool_limit, max_keepalive_connections=pool_limit, keepalive_expiry=120
        ),
        event_hooks={"request": [inspect_request]},
    )
    if MODE == "langchain":
        from langchain_openai import ChatOpenAI
        from pydantic import SecretStr

        MODEL = ChatOpenAI(
            model=BODY["model"],
            api_key=SecretStr(os.environ["BENCH_TOKEN"]),
            base_url=os.environ["BENCH_BASE"],
            default_headers={
                k: v
                for k, v in headers.items()
                if k.lower() not in {"authorization", "content-type"}
            },
            http_async_client=CLIENT,
            use_responses_api=True,
            reasoning_effort="low",
            store=False,
            max_retries=0,
            timeout=120,
            extra_body={"instructions": BODY["instructions"]},
        )


async def invoke(send: Any) -> None:
    text = ""
    completed = False
    model_name = None
    usage = {}
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/event-stream")],
        }
    )
    try:
        if MODE == "langchain":
            from langchain_core.messages import HumanMessage

            prompt = BODY["input"][0]["content"][0]["text"]
            async for chunk in MODEL.astream(
                [HumanMessage(content=[{"type": "text", "text": prompt}])]
            ):
                delta = chunk.text
                completed |= chunk.response_metadata.get("status") == "completed"
                if chunk.response_metadata.get("status") == "completed":
                    model_name = chunk.response_metadata.get("model")
                    usage = {
                        k: v
                        for k, v in (chunk.usage_metadata or {}).items()
                        if k in {"input_tokens", "output_tokens", "total_tokens"}
                    }
                if delta:
                    text += delta
                    await send(
                        {
                            "type": "http.response.body",
                            "body": frame({"delta": delta}),
                            "more_body": True,
                        }
                    )
        else:
            async with CLIENT.stream(
                "POST", os.environ["BENCH_BASE"] + "/responses", json=BODY
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    event = json.loads(line[6:])
                    if event["type"] == "response.output_text.delta":
                        delta = event["delta"]
                        text += delta
                        await send(
                            {
                                "type": "http.response.body",
                                "body": frame({"delta": delta}),
                                "more_body": True,
                            }
                        )
                    elif event["type"] == "response.completed":
                        completed = True
                        model_name = event["response"]["model"]
                        usage = {
                            k: event["response"]["usage"][k]
                            for k in ("input_tokens", "output_tokens", "total_tokens")
                        }
                    elif event["type"] in {"error", "response.failed", "response.incomplete"}:
                        raise RuntimeError("upstream failed or incomplete")
        if not completed or text != SCENARIO["expected"] or model_name != BODY["model"]:
            raise ValueError("stream incomplete or output differs")
        COUNTS["completed"] += 1
        await send(
            {
                "type": "http.response.body",
                "body": frame({"done": True, "text": text, "model": model_name, "usage": usage}),
            }
        )
    except Exception as exc:
        COUNTS["failures"] += 1
        await send({"type": "http.response.body", "body": frame({"error": type(exc).__name__})})


def frame(payload: dict[str, Any]) -> bytes:
    return ("data: " + json.dumps(payload, separators=(",", ":")) + "\n\n").encode()


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    if scope["type"] == "lifespan":
        while True:
            event = await receive()
            if event["type"] == "lifespan.startup":
                await start()
                await send({"type": "lifespan.startup.complete"})
            elif event["type"] == "lifespan.shutdown":
                await CLIENT.aclose()
                await send({"type": "lifespan.shutdown.complete"})
                return
    elif scope["type"] == "http":
        if scope["method"] == "POST" and scope["path"] == "/invoke":
            await receive()
            await invoke(send)
        else:
            payload = {"framework": MODE, "pid": os.getpid(), "resources": resources(), **COUNTS}
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": json.dumps(payload).encode()})


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=PORT,
        loop="asyncio",
        http="h11",
        workers=1,
        access_log=False,
        log_level="error",
        timeout_keep_alive=120,
    )

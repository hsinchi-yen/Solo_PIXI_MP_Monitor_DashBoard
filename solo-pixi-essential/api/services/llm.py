"""Local LLM client (llama.cpp, OpenAI-compatible) and its health status.

Status = result of the last real call, plus a light /models probe every
LLM_STATUS_PROBE_SEC when nothing else has talked to the model recently.
"""
import json
import logging
import threading
import time
import urllib.error
import urllib.request

import config

log = logging.getLogger("pixi.llm")

_status = {"connected": False, "checked_at": None, "last_ok_at": None, "last_error": None}
_status_lock = threading.Lock()


def _mark(ok, error=None):
    now = time.time()
    with _status_lock:
        _status["connected"] = ok
        _status["checked_at"] = now
        if ok:
            _status["last_ok_at"] = now
            _status["last_error"] = None
        else:
            _status["last_error"] = (str(error) or "error")[:200]


def status():
    with _status_lock:
        return dict(_status, model=config.LLM_MODEL)


def probe():
    try:
        req = urllib.request.Request(f"{config.LLM_API_BASE}/models",
                                     headers={"Authorization": f"Bearer {config.LLM_API_KEY}"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            _mark(resp.status == 200, None if resp.status == 200 else f"HTTP {resp.status}")
    except Exception as exc:
        _mark(False, exc)


def probe_if_stale():
    with _status_lock:
        checked = _status["checked_at"]
    if checked is None or time.time() - checked >= config.LLM_STATUS_PROBE_SEC:
        probe()


def _request(payload, stream=False, timeout=None):
    data = {"model": config.LLM_MODEL,
            "chat_template_kwargs": {"enable_thinking": config.LLM_ENABLE_THINKING},
            "stream": stream, **payload}
    req = urllib.request.Request(
        f"{config.LLM_API_BASE}/chat/completions",
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {config.LLM_API_KEY}"},
    )
    return urllib.request.urlopen(req, timeout=timeout or config.LLM_CHAT_TIMEOUT)


def complete(messages, timeout=None, **params):
    """Non-streaming call → message content. Raises on transport errors."""
    try:
        with _request({"messages": messages, **params}, timeout=timeout) as resp:
            content = json.loads(resp.read().decode("utf-8"))["choices"][0]["message"]["content"]
        _mark(True)
        return content
    except Exception as exc:
        _mark(False, exc)
        raise


def complete_json(messages, schema, name="result", timeout=None, **params):
    """Structured output via response_format json_schema → parsed dict."""
    content = complete(messages, timeout=timeout,
                       response_format={"type": "json_schema", "json_schema": {"name": name, "schema": schema}},
                       **params)
    return json.loads(content)


def stream_text(messages, timeout=None, **params):
    """Yield content deltas from an SSE stream."""
    try:
        with _request({"messages": messages, **params}, stream=True, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                choices = json.loads(chunk).get("choices") or []
                delta = (choices[0].get("delta") or {}).get("content") if choices else None
                if delta:
                    yield delta
        _mark(True)
    except GeneratorExit:
        raise
    except Exception as exc:
        _mark(False, exc)
        raise

"""Local LLM client (OpenAI-compatible: vLLM or llama.cpp) and its health status.

Status = result of the last real call, plus a light /models probe every
LLM_STATUS_PROBE_SEC when nothing else has talked to the model recently.

Server differences handled here so callers never see them:
- model id: vLLM rejects an unknown id with 404, llama.cpp mostly ignores it. The configured
  LLM_MODEL is used when the server lists it; otherwise the first listed id is used (re-resolved
  on a 404, so a model rename on the LLM host needs no redeploy here).
- reasoning: vLLM returns it in `reasoning`, llama.cpp in `reasoning_content` or inline as
  <think>...</think> in the content. Only the answer text is returned / streamed.
- structured output: `json_schema` response_format first; servers that reject it get
  `json_object` + schema (older llama.cpp), and the JSON is pulled out of fences / extra text.
"""
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request

import config

log = logging.getLogger("pixi.llm")

_status = {"connected": False, "checked_at": None, "last_ok_at": None, "last_error": None, "backend": None}
_status_lock = threading.Lock()
_resolved_model = None          # model id actually sent; None until the first /models listing

_THINK_RE = re.compile(r"<think>.*?</think>", re.S)
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


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


def active_model():
    """The model id requests use (resolved from /models, else the configured one)."""
    return _resolved_model or config.LLM_MODEL


def status():
    with _status_lock:
        return dict(_status, model=active_model())


def _headers():
    headers = {"Content-Type": "application/json"}
    if config.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {config.LLM_API_KEY}"
    return headers


def _resolve_model(ids):
    """Configured id if served, else the first served id (llama.cpp: its single loaded model)."""
    wanted = config.LLM_MODEL
    if wanted and wanted.lower() != "auto" and (wanted in ids or not ids):
        return wanted
    if ids:
        if wanted and wanted.lower() != "auto":
            log.warning("LLM model %r is not served by %s; using %r (served: %s)",
                        wanted, config.LLM_API_BASE, ids[0], ", ".join(ids))
        return ids[0]
    return wanted or None


def probe():
    """GET /models → connection status, backend name and the model id to use."""
    global _resolved_model
    try:
        req = urllib.request.Request(f"{config.LLM_API_BASE}/models", headers=_headers())
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
        data = body.get("data") if isinstance(body, dict) else None
        entries = [m for m in (data or []) if isinstance(m, dict) and m.get("id")]
        ids = [m["id"] for m in entries]
        model = _resolve_model(ids)
        if model != _resolved_model:
            log.info("LLM model in use: %s", model)
        _resolved_model = model
        with _status_lock:
            _status["backend"] = (entries[0].get("owned_by") if entries else None)
        _mark(bool(model), None if model else "no model served")
    except Exception as exc:
        _mark(False, exc)


def probe_if_stale():
    with _status_lock:
        checked = _status["checked_at"]
    if checked is None or time.time() - checked >= config.LLM_STATUS_PROBE_SEC:
        probe()


def _open(payload, stream, timeout):
    if _resolved_model is None:
        probe()
    data = {"model": active_model(),
            "chat_template_kwargs": {"enable_thinking": config.LLM_ENABLE_THINKING},
            "stream": stream, **payload}
    req = urllib.request.Request(f"{config.LLM_API_BASE}/chat/completions",
                                 data=json.dumps(data).encode("utf-8"), headers=_headers())
    return urllib.request.urlopen(req, timeout=timeout or config.LLM_CHAT_TIMEOUT)


def _request(payload, stream=False, timeout=None):
    """POST /chat/completions; on 404 (model renamed / unloaded) re-resolve the model once and retry."""
    try:
        return _open(payload, stream, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        before = active_model()
        probe()
        if active_model() == before:
            raise
        return _open(payload, stream, timeout)


def _http_error_text(exc):
    try:
        return exc.read().decode("utf-8", "replace")[:300]
    except Exception:
        return ""


def clean_content(content):
    """Answer text only: no <think> blocks (closed or cut off), no surrounding whitespace."""
    if not isinstance(content, str):
        return ""
    text = _THINK_RE.sub("", content)
    if "<think>" in text:                      # reasoning cut off by max_tokens: no answer follows
        text = text.split("<think>", 1)[0]
    if "</think>" in text:                     # template opened the block in the prompt
        text = text.rsplit("</think>", 1)[1]
    return text.strip()


def complete(messages, timeout=None, **params):
    """Non-streaming call → message content (answer text only). Raises on transport errors."""
    try:
        with _request({"messages": messages, **params}, timeout=timeout) as resp:
            content = json.loads(resp.read().decode("utf-8"))["choices"][0]["message"].get("content")
        _mark(True)
        return clean_content(content)
    except urllib.error.HTTPError as exc:
        _mark(False, f"HTTP {exc.code}: {_http_error_text(exc)}")
        raise
    except Exception as exc:
        _mark(False, exc)
        raise


def parse_json_content(content):
    """JSON from a model reply that may carry ``` fences or text around the object."""
    text = (content or "").strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("LLM reply contains no JSON object")
        return json.loads(text[start:end + 1])


def complete_json(messages, schema, name="result", timeout=None, **params):
    """Structured output → parsed dict. json_schema first; json_object + schema if the server rejects it."""
    try:
        content = complete(messages, timeout=timeout,
                           response_format={"type": "json_schema", "json_schema": {"name": name, "schema": schema}},
                           **params)
    except urllib.error.HTTPError as exc:
        if exc.code not in (400, 422, 501):
            raise
        log.info("LLM server rejected json_schema (HTTP %s); retrying with json_object", exc.code)
        content = complete(messages, timeout=timeout,
                           response_format={"type": "json_object", "schema": schema}, **params)
    return parse_json_content(content)


class _ThinkFilter:
    """Drop <think>...</think> spans and leading whitespace from a stream of text deltas."""
    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self):
        self.buf = ""
        self.in_think = False
        self.started = False

    def feed(self, text):
        self.buf += text
        out = []
        while self.buf:
            tag = self.CLOSE if self.in_think else self.OPEN
            idx = self.buf.find(tag)
            if idx >= 0:
                if not self.in_think:
                    out.append(self.buf[:idx])
                self.buf = self.buf[idx + len(tag):]
                self.in_think = not self.in_think
                continue
            keep = _partial_suffix(self.buf, tag)   # a tag may be split across deltas
            if not self.in_think:
                out.append(self.buf[:len(self.buf) - keep])
            self.buf = self.buf[len(self.buf) - keep:]
            break
        return self._emit("".join(out))

    def flush(self):
        rest, self.buf = ("" if self.in_think else self.buf), ""
        return self._emit(rest)

    def _emit(self, text):
        if not self.started:
            text = text.lstrip()
            self.started = bool(text)
        return text


def _partial_suffix(text, tag):
    for n in range(min(len(tag) - 1, len(text)), 0, -1):
        if text.endswith(tag[:n]):
            return n
    return 0


def stream_text(messages, timeout=None, **params):
    """Yield answer-text deltas from an SSE stream (reasoning deltas are skipped)."""
    filt = _ThinkFilter()
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
                    text = filt.feed(delta)
                    if text:
                        yield text
            tail = filt.flush()
            if tail:
                yield tail
        _mark(True)
    except GeneratorExit:
        raise
    except urllib.error.HTTPError as exc:
        _mark(False, f"HTTP {exc.code}: {_http_error_text(exc)}")
        raise
    except Exception as exc:
        _mark(False, exc)
        raise

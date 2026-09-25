"""LLM backends shared by every method.

Every self-improvement loop in this package talks to language models through the
small :class:`LLM` interface below, so the same loop can run against

* :class:`MockLLM`   - a deterministic Python function (unit tests, CPU demos, CI),
* :class:`ClaudeCLI` - the headless ``claude -p`` command (no API key needed where
  Claude Code is logged in),
* :class:`AnthropicAPI` - the Messages API when ``ANTHROPIC_API_KEY`` is set.

All backends meter usage (calls, input/output tokens, dollars) per *role*
("proposer", "critic", "task", ...), which is what the cost rules of RRSI,
SoL-Pi and Dream-RSI consume.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional

MODEL_ALIASES = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-5",
    "opus": "claude-opus-5-5",
    "fable": "claude-fable-5-1",
}


def resolve_model(name: str) -> str:
    return MODEL_ALIASES.get(name, name)


def estimate_tokens(text: str) -> int:
    """Cheap, backend-independent token estimate (~4 chars per token)."""
    return int(math.ceil(len(text or "") / 4))


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.calls + other.calls,
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cost_usd + other.cost_usd,
            self.latency_s + other.latency_s,
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total_tokens"] = self.total_tokens
        return d


@dataclass
class LLMResponse:
    text: str
    usage: Usage
    model: str
    raw: Optional[dict] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


class UsageMeter:
    """Thread-safe usage accumulator, keyed by role."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.by_role: dict[str, Usage] = {}

    def add(self, role: str, usage: Usage) -> None:
        with self._lock:
            self.by_role[role] = self.by_role.get(role, Usage()) + usage

    def total(self) -> Usage:
        with self._lock:
            out = Usage()
            for u in self.by_role.values():
                out = out + u
            return out

    def snapshot(self) -> dict:
        with self._lock:
            d = {k: v.to_dict() for k, v in self.by_role.items()}
        d["_total"] = self.total().to_dict()
        return d

    def reset(self) -> None:
        with self._lock:
            self.by_role.clear()


class LLM:
    """Base class. Subclasses implement :meth:`_complete`."""

    name: str = "llm"

    def __init__(self) -> None:
        self.meter = UsageMeter()

    def complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        seed: Optional[int] = None,
        role: str = "default",
    ) -> LLMResponse:
        """Return one completion. ``seed`` distinguishes repeated samples of the
        same prompt (used by caches and by the mock); real backends are
        stochastic regardless."""
        resp = self._complete(prompt, system=system, max_tokens=max_tokens, seed=seed)
        self.meter.add(role, resp.usage)
        return resp

    def _complete(self, prompt, *, system, max_tokens, seed) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name})"


# --------------------------------------------------------------------------- mock
class MockLLM(LLM):
    """Deterministic LLM for tests and CPU demos.

    ``responder(prompt, system, seed, call_index) -> str`` decides the reply.
    With no responder, it echoes a fixed string. Token usage is estimated from
    text length so cost-aware rules still see realistic numbers.
    """

    def __init__(self, responder: Optional[Callable[..., str]] = None, name: str = "mock") -> None:
        super().__init__()
        self.responder = responder or (lambda prompt, system, seed, i: "OK")
        self.name = name
        self._i = 0
        self._lock = threading.Lock()
        self.calls: list[dict] = []

    def _complete(self, prompt, *, system, max_tokens, seed) -> LLMResponse:
        with self._lock:
            i = self._i
            self._i += 1
        text = self.responder(prompt, system, seed, i)
        with self._lock:
            self.calls.append({"prompt": prompt, "system": system, "seed": seed, "reply": text})
        usage = Usage(1, estimate_tokens((system or "") + prompt), estimate_tokens(text), 0.0, 0.0)
        return LLMResponse(text=text, usage=usage, model=self.name)


# --------------------------------------------------------------------- claude CLI
DEFAULT_SYSTEM = "You are a careful, concise assistant."

_PRICES_PER_MTOK = {  # (input, output) USD per million tokens; only used when a backend reports no cost
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}


class ClaudeCLI(LLM):
    """Headless ``claude -p`` backend.

    Runs lean: custom system prompt, no tools, no MCP, no settings, no session
    persistence, so each call costs roughly prompt + reply tokens.
    """

    def __init__(
        self,
        model: str = "haiku",
        *,
        timeout_s: float = 300.0,
        retries: int = 3,
        effort: Optional[str] = None,
        executable: str = "claude",
    ) -> None:
        super().__init__()
        self.model = resolve_model(model)
        self.name = f"claude-cli:{self.model}"
        self.timeout_s = timeout_s
        self.retries = retries
        self.effort = effort
        self.executable = executable

    def base_cmd(self, system: Optional[str]) -> list[str]:
        cmd = [
            self.executable, "-p",
            "--model", self.model,
            "--output-format", "json",
            "--system-prompt", system or DEFAULT_SYSTEM,
            "--no-session-persistence",
            "--strict-mcp-config",
            "--setting-sources", "",
        ]
        if self.effort:
            cmd += ["--effort", self.effort]
        return cmd

    def _run(self, cmd: list[str], prompt: str, cwd: Optional[str] = None, timeout: Optional[float] = None) -> LLMResponse:
        last_err = "unknown"
        for attempt in range(self.retries):
            t0 = time.time()
            try:
                proc = subprocess.run(
                    cmd, input=prompt, capture_output=True, text=True,
                    timeout=timeout or self.timeout_s, cwd=cwd,
                )
            except subprocess.TimeoutExpired:
                last_err = "timeout"
                continue
            dt = time.time() - t0
            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                last_err = (proc.stderr or proc.stdout or "no output")[-500:]
                time.sleep(2 ** attempt)
                continue
            if isinstance(data, list):  # verbose/stream-style output: a list of messages ending in the result
                data = next((m for m in reversed(data) if isinstance(m, dict) and m.get("type") == "result"), None)
            if not isinstance(data, dict):
                last_err = f"unexpected CLI output: {proc.stdout[-300:]}"
                time.sleep(2 ** attempt)
                continue
            if data.get("is_error"):
                last_err = str(data.get("result") or data.get("subtype") or "is_error")[-500:]
                time.sleep(2 ** attempt)
                continue
            u = data.get("usage") or {}
            in_tok = int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0)) + int(
                u.get("cache_creation_input_tokens", 0))
            out_tok = int(u.get("output_tokens", 0))
            cost = float(data.get("total_cost_usd") or 0.0)
            usage = Usage(1, in_tok, out_tok, cost, dt)
            return LLMResponse(text=data.get("result") or "", usage=usage, model=self.model, raw=data)
        return LLMResponse(text="", usage=Usage(1, estimate_tokens(prompt), 0, 0.0, 0.0), model=self.model,
                           error=last_err)

    def _complete(self, prompt, *, system, max_tokens, seed) -> LLMResponse:
        cmd = self.base_cmd(system) + ["--tools", ""]
        return self._run(cmd, prompt)

    def run_agent(
        self,
        prompt: str,
        *,
        cwd: str,
        system: Optional[str] = None,
        tools: tuple[str, ...] = ("Read", "Edit", "Write", "Glob", "Grep"),
        timeout_s: Optional[float] = None,
        role: str = "agent",
    ) -> LLMResponse:
        """Run ``claude -p`` as a coding agent confined to ``cwd`` with file tools."""
        cmd = self.base_cmd(system) + ["--tools", ",".join(tools), "--permission-mode", "acceptEdits",
                                       "--add-dir", cwd]
        resp = self._run(cmd, prompt, cwd=cwd, timeout=timeout_s)
        self.meter.add(role, resp.usage)
        return resp


# -------------------------------------------------------------------- Anthropic API
class AnthropicAPI(LLM):
    """Messages API backend (needs ``pip install anthropic`` and ANTHROPIC_API_KEY)."""

    def __init__(self, model: str = "haiku", *, max_tokens: int = 4096, retries: int = 4) -> None:
        super().__init__()
        import anthropic  # local import: optional dependency

        self.client = anthropic.Anthropic()
        self.model = resolve_model(model)
        self.name = f"api:{self.model}"
        self.max_tokens = max_tokens
        self.retries = retries

    def _complete(self, prompt, *, system, max_tokens, seed) -> LLMResponse:
        t0 = time.time()
        last = "unknown"
        for attempt in range(self.retries):
            try:
                msg = self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens or self.max_tokens,
                    system=system or DEFAULT_SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = "".join(getattr(b, "text", "") for b in msg.content)
                pin, pout = _PRICES_PER_MTOK.get(self.model, (0.0, 0.0))
                cost = (msg.usage.input_tokens * pin + msg.usage.output_tokens * pout) / 1e6
                usage = Usage(1, msg.usage.input_tokens, msg.usage.output_tokens, cost, time.time() - t0)
                return LLMResponse(text=text, usage=usage, model=self.model)
            except Exception as e:  # noqa: BLE001 - network errors vary by SDK version
                last = repr(e)
                time.sleep(2 ** attempt)
        return LLMResponse(text="", usage=Usage(1, 0, 0, 0.0, time.time() - t0), model=self.model, error=last)


# ------------------------------------------------------------------------- cache
class CachedLLM(LLM):
    """Disk cache in front of another backend.

    The key includes ``seed``, so repeated trials of the same prompt (seed=0,1,2..)
    stay distinct while a re-run of a whole experiment replays for free.
    Cached hits are metered with zero calls/cost but their original token counts
    are kept under ``role + ':cached'`` for honest reporting, so ``meter.total()``
    (and anything summing roles) reports real spend only. The original usage of
    cached hits (including the dollars they saved) is kept in ``saved``;
    ``hits`` / ``misses`` count lookups. A hit's response carries
    ``raw = {"cached": True, "usage": <original usage dict>}``, so a domain that
    records the *artifact's* inference cost (not the spend) can report the same
    dollars on a replay as on the original run (see :func:`artifact_usage`).
    """

    def __init__(self, inner: LLM, cache_dir: str | Path, *, offline: bool = False) -> None:
        super().__init__()
        self.inner = inner
        #: offline=True replays recorded runs for $0: a cache miss returns an error response
        #: instead of calling the backend.
        self.offline = offline
        self.name = f"cached:{inner.name}"
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.saved = UsageMeter()
        self.hits = 0
        self.misses = 0
        self._count_lock = threading.Lock()

    def _key(self, prompt, system, max_tokens, seed) -> Path:
        h = hashlib.sha256(json.dumps([self.inner.name, system, prompt, max_tokens, seed]).encode()).hexdigest()
        return self.dir / h[:2] / f"{h}.json"

    def _read(self, path: Path) -> Optional[dict]:
        try:
            d = json.loads(path.read_text())
            Usage(**d["usage"])  # validate
            return d if isinstance(d.get("text"), str) else None
        except (OSError, ValueError, KeyError, TypeError):
            return None  # missing or corrupt entry -> treat as a miss (it is rewritten below)

    def complete(self, prompt, *, system=None, max_tokens=None, seed=None, role="default") -> LLMResponse:
        path = self._key(prompt, system, max_tokens, seed)
        d = self._read(path) if path.exists() else None
        if d is not None:
            u = Usage(**d["usage"])
            self.meter.add(role + ":cached", Usage(0, u.input_tokens, u.output_tokens, 0.0, 0.0))
            self.saved.add(role, u)
            with self._count_lock:
                self.hits += 1
            return LLMResponse(text=d["text"], usage=Usage(0, u.input_tokens, u.output_tokens, 0.0, 0.0),
                               model=d.get("model", self.inner.name), raw={"cached": True, "usage": asdict(u)})
        with self._count_lock:
            self.misses += 1
        if self.offline:
            return LLMResponse(text="", usage=Usage(), model=self.inner.name,
                               error="infra: cache miss in offline replay mode")
        resp = self.inner.complete(prompt, system=system, max_tokens=max_tokens, seed=seed, role=role)
        self.meter.add(role, resp.usage)
        if resp.ok:
            path.parent.mkdir(parents=True, exist_ok=True)
            # unique temp name: concurrent writers of the same key must not race on one temp file
            tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps({"text": resp.text, "usage": asdict(resp.usage), "model": resp.model}))
            tmp.replace(path)
        return resp


def artifact_usage(resp: LLMResponse) -> Usage:
    """The usage a response *represents* - for :class:`CachedLLM` hits the original
    call's tokens and dollars, otherwise ``resp.usage``. Use it for an artifact's
    inference-cost metrics (so replays match fresh runs); use ``resp.usage`` /
    meters for money actually spent."""
    raw = resp.raw if isinstance(resp.raw, dict) else {}
    if raw.get("cached") and isinstance(raw.get("usage"), dict):
        try:
            return Usage(**raw["usage"])
        except TypeError:
            pass
    return resp.usage


def get_llm(spec: str = "mock", *, cache_dir: Optional[str] = None, **kw: Any) -> LLM:
    """Build a backend from a short spec string.

    ``mock`` | ``claude:<model>`` (headless CLI) | ``api:<model>`` | ``auto:<model>``
    (API if ANTHROPIC_API_KEY is set, else CLI).
    """
    kind, _, model = spec.partition(":")
    model = model or "haiku"
    if kind == "mock":
        llm: LLM = MockLLM()
    elif kind == "claude":
        llm = ClaudeCLI(model, **kw)
    elif kind == "api":
        llm = AnthropicAPI(model, **kw)
    elif kind == "auto":
        llm = AnthropicAPI(model, **kw) if os.environ.get("ANTHROPIC_API_KEY") else ClaudeCLI(model, **kw)
    else:
        raise ValueError(f"unknown LLM spec {spec!r}")
    return CachedLLM(llm, cache_dir) if cache_dir else llm


# ------------------------------------------------------------------ text parsing
_FENCE = re.compile(r"```([A-Za-z0-9_+\-.]*)[^\n]*\n(.*?)```", re.S)


def extract_code_blocks(text: str, lang: Optional[str] = None) -> list[str]:
    """All fenced code blocks, optionally filtered by language tag."""
    out = []
    for tag, body in _FENCE.findall(text or ""):
        if lang is None or tag.lower() == lang.lower():
            out.append(body)
    return out


def extract_code(text: str, lang: Optional[str] = "python") -> Optional[str]:
    """The longest fenced block of ``lang`` (falls back to any fence)."""
    blocks = extract_code_blocks(text, lang) or extract_code_blocks(text)
    return max(blocks, key=len) if blocks else None


def extract_json(text: str) -> Any:
    """Parse the first JSON object/array in ``text`` (fenced or bare). Raises ValueError.

    Order: ```json fences, then any fence, then the first balanced ``{...}`` in
    the text, then the first balanced ``[...]``. A fence holding a bare scalar
    (e.g. a fenced number) is returned only when no object/array is found."""
    scalar_found, scalar = False, None
    for block in extract_code_blocks(text, "json") + extract_code_blocks(text):
        try:
            v = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(v, (dict, list)):
            return v
        if not scalar_found:
            scalar_found, scalar = True, v
    s = text or ""
    for opener, closer in (("{", "}"), ("[", "]")):
        start = s.find(opener)
        while start != -1:
            depth, in_str, esc = 0, False, False
            for i in range(start, len(s)):
                ch = s[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                elif ch == '"':
                    in_str = True
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(s[start:i + 1])
                        except json.JSONDecodeError:
                            break
            start = s.find(opener, start + 1)
    if scalar_found:
        return scalar
    raise ValueError("no JSON found in text")


def extract_tag(text: str, tag: str) -> Optional[str]:
    """Content of ``<tag>...</tag>`` (last occurrence), or None."""
    m = re.findall(rf"<{tag}>(.*?)</{tag}>", text or "", re.S)
    return m[-1].strip() if m else None

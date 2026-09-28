"""Pre-evaluation checks: interface validation and the (optional) leakage screen.

* :class:`InterfaceValidator` - Meta-Harness's ``validate_candidates``: every
  Python file must compile, then the domain's ``smoke`` (import, instantiate, call
  the interface on a tiny input) must pass within ``timeout_s`` (release: "prints
  OK within 30 s"). With ``isolate=True`` the smoke runs in a forked child that is
  killed on timeout, so a hung candidate cannot stall the loop.
* :class:`LeakageScreen` - OFF by default (the paper's loop has no mechanical
  guard; spec A8.2). ON reproduces the experimental pilot's ``validate_source``:
  a case-insensitive forbidden-reference check over the candidate source, plus a
  shape check for large hard-coded string tables. Uses :class:`rsi.core.LeakageCritic`
  for the denylist (whole-token matching over added lines).
"""
from __future__ import annotations

import ast
import os
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional

from ..core.artifact import Artifact
from ..core.critic import LeakageCritic

#: pilot's UNIVERSAL_FORBIDDEN adapted to this framework's store layout
UNIVERSAL_FORBIDDEN = ("results/", "test.json", "finalized.json", "frontier.json", "/tests", "verifier",
                       "/solution")


@dataclass
class InterfaceValidator:
    timeout_s: float = 30.0
    isolate: bool = True

    def validate(self, domain, artifact: Artifact, llm=None) -> tuple[bool, str]:
        for name, text in artifact.items():
            if name.endswith(".py"):
                try:
                    compile(text, name, "exec")
                except SyntaxError as e:
                    return False, f"syntax error in {name}: {e}"
        if not self.isolate or not hasattr(os, "fork"):
            try:
                err = domain.smoke(artifact, llm)
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
            return (err is None), (err or "OK")
        return self._forked(domain, artifact, llm)

    def _forked(self, domain, artifact: Artifact, llm) -> tuple[bool, str]:
        """Run the smoke in a raw forked child (works inside daemonic pool workers too);
        kill it on timeout.

        The child's model calls are real spend that the parent's meters would never see (live: $0.02-0.03 per
        run missing from the loop meter, fix 17). Every usage the child meters is streamed back through the pipe
        *as it happens*, so a child killed on timeout still reports the calls it completed (register #24). A
        call still in flight when the child is killed has no usage yet; it is counted in the returned message."""
        import pickle
        import select
        import signal
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:                                     # child
            os.close(r)
            try:
                lock = threading.Lock()

                def send(msg) -> None:
                    data = pickle.dumps(msg)
                    with lock:
                        os.write(w, len(data).to_bytes(8, "big") + data)

                _stream_usage(llm, send)
                try:
                    err = domain.smoke(artifact, llm)
                except Exception as e:  # noqa: BLE001
                    err = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
                send(("done", err))
            finally:
                os._exit(0)
        os.close(w)
        deadline = time.monotonic() + self.timeout_s
        done, err, buf = False, None, b""
        inflight: dict[int, int] = {}
        try:
            while not done:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                ready, _, _ = select.select([r], [], [], left)
                if not ready:
                    break
                chunk = os.read(r, 1 << 16)
                if not chunk:                             # child exited (or died) without "done"
                    break
                buf += chunk
                while len(buf) >= 8 and len(buf) >= 8 + int.from_bytes(buf[:8], "big"):
                    n = int.from_bytes(buf[:8], "big")
                    msg, buf = pickle.loads(buf[8:8 + n]), buf[8 + n:]
                    if msg[0] == "usage":                 # ("usage", meter index, role, usage dict)
                        _apply_one(llm, msg[1], msg[2], msg[3])
                    elif msg[0] == "call":                # ("call", +1 started | -1 finished)
                        inflight[0] = inflight.get(0, 0) + msg[1]
                    elif msg[0] == "done":
                        done, err = True, msg[1]
        finally:
            os.close(r)
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)
        if not done:
            if time.monotonic() >= deadline:
                err = f"smoke timed out after {self.timeout_s}s"
                if inflight.get(0, 0) > 0:
                    err += f" ({inflight[0]} model call(s) in flight at the kill: their usage is unknown)"
            else:
                err = "smoke process died"
        return (err is None), (err or "OK")


def _meters(llm) -> list:
    """The usage meters along a wrapper chain (e.g. CachedLLM -> ClaudeCLI)."""
    out, seen = [], set()
    while llm is not None and id(llm) not in seen:
        seen.add(id(llm))
        m = getattr(llm, "meter", None)
        if m is not None and hasattr(m, "by_role"):
            out.append(m)
        llm = getattr(llm, "__dict__", {}).get("inner")
    return out


def _stream_usage(llm, send) -> None:
    """In the forked child: make every meter along the wrapper chain forward each ``add`` to the parent
    (``send(("usage", meter index, role, usage dict))``), and bracket every model call with
    ``("call", +1)`` / ``("call", -1)`` so the parent knows how many calls were in flight at a kill."""
    for i, m in enumerate(_meters(llm)):
        orig = m.add

        def add(role, usage, _orig=orig, _i=i):
            _orig(role, usage)
            send(("usage", _i, role, asdict(usage)))

        try:
            m.add = add                               # the child's copy only (fork), never the parent's
        except AttributeError:                        # a meter that forbids instance attributes: no streaming
            pass
    if llm is not None and hasattr(llm, "complete"):
        orig_complete = llm.complete

        def complete(*a, **kw):
            send(("call", 1))
            try:
                return orig_complete(*a, **kw)
            finally:
                send(("call", -1))

        try:
            llm.complete = complete
        except AttributeError:
            pass


def _apply_one(llm, idx: int, role: str, u: dict) -> None:
    """Add one usage record streamed by the forked smoke to the parent's matching meter."""
    from ..core.llm import Usage
    ms = _meters(llm)
    if 0 <= idx < len(ms):
        ms[idx].add(role, Usage(**u))


@dataclass
class LeakageScreen:
    """Forbidden-reference + table-shape screen (the pilot's pre-evaluation check)."""

    forbidden: list[str] = field(default_factory=list)
    universal: tuple[str, ...] = UNIVERSAL_FORBIDDEN
    max_literal_strings: int = 25
    llm: Optional[object] = None
    domain_brief: str = ""
    n_screened: int = 0
    n_rejected: int = 0

    def __post_init__(self) -> None:
        self.critic = LeakageCritic(terms=list(self.forbidden) + list(self.universal), llm=self.llm,
                                    domain_brief=self.domain_brief, min_term_len=4)

    @classmethod
    def for_domain(cls, domain, llm=None) -> "LeakageScreen":
        return cls(forbidden=list(domain.leakage_terms("evolve")), llm=llm, domain_brief=domain.describe())

    def check(self, artifact: Artifact, base: Optional[Artifact] = None) -> Optional[str]:
        """Return a rejection reason or None."""
        self.n_screened += 1
        diff = (base or Artifact({})).diff(artifact)
        v = self.critic.screen(diff)
        if not v.accept:
            self.n_rejected += 1
            return f"leakage: {v.objections[0] if v.objections else v.stage}"
        for name, text in artifact.items():
            if name.endswith(".py"):
                n = _max_string_table(text)
                if n > self.max_literal_strings:
                    self.n_rejected += 1
                    return f"leakage: {name} hard-codes a table of {n} string literals"
        return None


def _max_string_table(src: str) -> int:
    """Largest dict/list/set literal made mostly of string constants (lookup-table shape)."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return 0
    best = 0
    for node in ast.walk(tree):
        elts: Iterable = ()
        if isinstance(node, ast.Dict):
            elts = list(node.keys) + list(node.values)
        elif isinstance(node, (ast.List, ast.Set, ast.Tuple)):
            elts = node.elts
        strs = [e for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if isinstance(node, ast.Dict):
            best = max(best, len([k for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]))
        else:
            best = max(best, len(strs))
    return best

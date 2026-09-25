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
import multiprocessing as mp
import os
import traceback
from dataclasses import dataclass, field
from typing import Iterable, Optional

from ..core.artifact import Artifact
from ..core.critic import LeakageCritic

#: pilot's UNIVERSAL_FORBIDDEN adapted to this framework's store layout
UNIVERSAL_FORBIDDEN = ("results/", "test.json", "finalized.json", "frontier.json", "/tests", "verifier",
                       "/solution")


def _smoke_child(conn, domain, artifact, llm) -> None:
    try:
        err = domain.smoke(artifact, llm)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
    try:
        conn.send(err)
    finally:
        conn.close()


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
        kill it on timeout."""
        import pickle
        import select
        import signal
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:                                     # child
            os.close(r)
            try:
                try:
                    err = domain.smoke(artifact, llm)
                except Exception as e:  # noqa: BLE001
                    err = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
                data = pickle.dumps(err)
                os.write(w, len(data).to_bytes(8, "big") + data)
            finally:
                os._exit(0)
        os.close(w)
        err: Optional[str] = f"smoke timed out after {self.timeout_s}s"
        try:
            ready, _, _ = select.select([r], [], [], self.timeout_s)
            if ready:
                buf = b""
                while True:
                    chunk = os.read(r, 1 << 16)
                    if not chunk:
                        break
                    buf += chunk
                if len(buf) >= 8:
                    n = int.from_bytes(buf[:8], "big")
                    err = pickle.loads(buf[8:8 + n])
                else:
                    err = "smoke process died"
        finally:
            os.close(r)
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)
        return (err is None), (err or "OK")
        return (err is None), (err or "OK")


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

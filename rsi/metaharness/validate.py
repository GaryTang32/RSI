"""Pre-evaluation checks: interface validation (fork-isolated smoke) and the optional leakage screen."""

from __future__ import annotations

import ast
import itertools
import os
import pickle
import select
import signal
import threading
import time
import traceback
from collections import Counter
from dataclasses import asdict, dataclass, field

from ..core import sandbox as _sandbox
from ..core.artifact import Artifact
from ..core.critic import LeakageCritic, added_lines
from ..core.llm import Usage

try:  # the C function sets the mask before running pending handlers (signal's wrapper may run them first)
    from _signal import pthread_sigmask as _c_sigmask
except ImportError:  # pragma: no cover - platforms without it never fork here
    _c_sigmask = None

#: the pilot's UNIVERSAL_FORBIDDEN adapted to this store layout
UNIVERSAL_FORBIDDEN = ("results/", "test.json", "finalized.json", "frontier.json", "/tests", "verifier", "/solution")


@dataclass
class InterfaceValidator:
    """Every Python file must compile, then ``domain.smoke`` must pass within ``timeout_s`` (forked if ``isolate``)."""

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
        """Smoke in a raw forked child that streams its metered usage back and is killed on timeout."""
        buf = b""
        # All signals are blocked across the fork, so the child gets none until its own handlers are set. In the
        # parent the pipe and pid are stored, and the write end popped and closed, with no bytecode in between,
        # so a stop signal handled at any point leaves a cleanup that is idempotent and retried until done.
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])  # the current mask; a pending signal raises here, first
        fds: list[int] = []
        pids: list[int] = []
        done, err, deadline = False, None, 0.0
        inflight: dict[int, int] = {}
        try:
            signal.pthread_sigmask(signal.SIG_BLOCK, signal.valid_signals())
            # extend() stores what the call returns from C, before any bytecode could run a signal handler
            fds.extend(itertools.chain.from_iterable(itertools.starmap(os.pipe, [()])))
            pids.extend(itertools.starmap(os.fork, [()]))
            if pids[0] == 0:  # child
                try:
                    self._child(domain, artifact, llm, fds[0], fds[1], mask)
                finally:
                    os._exit(1)
            _pop_close(fds)  # the write end
            _c_sigmask(signal.SIG_SETMASK, mask)  # sets the mask, then runs what is pending
            r = fds[0]
            deadline = time.monotonic() + self.timeout_s
            while not done:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                ready, _, _ = select.select([r], [], [], left)
                if not ready:
                    break
                chunk = os.read(r, 1 << 16)
                if not chunk:  # child exited (or died) without "done"
                    break
                buf += chunk
                while len(buf) >= 8 and len(buf) >= 8 + int.from_bytes(buf[:8], "big"):
                    n = int.from_bytes(buf[:8], "big")
                    msg, buf = pickle.loads(buf[8 : 8 + n]), buf[8 + n :]
                    if msg[0] == "usage":  # ("usage", meter index, role, usage dict)
                        _apply_one(llm, msg[1], msg[2], msg[3])
                    elif msg[0] == "call":  # ("call", +1 started | -1 finished)
                        inflight[0] = inflight.get(0, 0) + msg[1]
                    elif msg[0] == "done":
                        done, err = True, msg[1]
        finally:
            # retried with less grace each time if another stop signal lands here; nested so that every point where
            # a pending handler can run is inside a try. The first interruption is raised afterwards
            try:
                _cleanup_fork(fds, pids, mask, 2.0)
            except BaseException:
                try:
                    _cleanup_fork(fds, pids, mask, 0.5)
                except BaseException:
                    try:
                        _cleanup_fork(fds, pids, mask, 0.0)
                    except BaseException:
                        _cleanup_fork(fds, pids, mask, 0.0)
                raise
        if not done:
            if time.monotonic() >= deadline:
                err = f"smoke timed out after {self.timeout_s}s"
                if inflight.get(0, 0) > 0:
                    err += f" ({inflight[0]} model call(s) in flight at the kill: their usage is unknown)"
            else:
                err = "smoke process died"
        return (err is None), (err or "OK")

    def _child(self, domain, artifact: Artifact, llm, r: int, w: int, mask) -> None:
        """The forked smoke child: run the smoke, stream usage through ``w``, leave via :func:`_child_exit`."""
        exiting = [False]

        def on_signal(signum, frame) -> None:
            # SIGTERM / SIGINT unwind through the finally below, which closes the sandboxes the smoke started
            if not exiting[0]:
                raise (SystemExit(143) if signum == signal.SIGTERM else KeyboardInterrupt())

        try:
            os.close(r)
            signal.signal(signal.SIGTERM, on_signal)
            signal.signal(signal.SIGINT, on_signal)
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)  # a signal held since the fork arrives now
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
            # first, before any bytecode that runs pending handlers: no signal can unwind into the parent's code
            exiting[0] = True
            try:
                _child_exit()
            finally:
                os._exit(0)


def _pop_close(fds: list) -> None:
    """Close and forget the last fd, with no bytecode between the pop and the close (both run from C)."""
    next(map(os.close, map(list.pop, [fds])))


def _cleanup_fork(fds: list, pids: list, mask, grace_s: float) -> None:
    """Restore the signal mask, close the rest of the pipe and stop the child; idempotent, so retries finish it."""
    _c_sigmask(signal.SIG_SETMASK, mask)
    while fds:
        _pop_close(fds)
    if pids and pids[0] > 0:
        _stop_child(pids[0], grace_s)
        pids.clear()


def _child_exit() -> None:
    """Close a leaving smoke child's sandboxes, ignoring SIGTERM/SIGINT; an interrupted close runs again."""
    for _ in range(100):
        try:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            _sandbox.close_sandboxes()
            return
        except BaseException:  # noqa: BLE001 - close again
            continue


def _stop_child(pid: int, grace_s: float = 2.0) -> None:
    """SIGTERM a smoke child until it exits; after ``grace_s`` kill its sandboxes and SIGKILL it."""

    def reaped() -> bool:
        try:
            return bool(os.waitpid(pid, os.WNOHANG)[0])
        except ChildProcessError:  # reaped already, by an earlier (interrupted) stop
            return True

    end, resend = time.monotonic() + grace_s, 0.0
    while not reaped():
        if time.monotonic() >= end:
            _sandbox.kill_child_sessions(pid)
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
            return
        if time.monotonic() >= resend:  # again now and then: one may have landed before the child could act on it
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            resend = time.monotonic() + 0.2
        time.sleep(0.01)


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
    """In the forked child: forward every meter ``add`` to the parent and bracket each model call."""
    for i, m in enumerate(_meters(llm)):
        orig = m.add

        def add(role, usage, _orig=orig, _i=i):
            _orig(role, usage)
            send(("usage", _i, role, asdict(usage)))

        try:
            m.add = add  # the child's copy only (fork), never the parent's
        except AttributeError:
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
    ms = _meters(llm)
    if 0 <= idx < len(ms):
        ms[idx].add(role, Usage(**u))


@dataclass
class LeakageScreen:
    """Forbidden-reference + table-shape screen (the pilot's pre-evaluation check; off in the paper's loop)."""

    forbidden: list[str] = field(default_factory=list)
    universal: tuple[str, ...] = UNIVERSAL_FORBIDDEN
    max_literal_strings: int = 25
    llm: object | None = None
    domain_brief: str = ""
    n_screened: int = 0
    n_rejected: int = 0

    def __post_init__(self) -> None:
        terms = [str(t) for t in (*self.forbidden, *self.universal)]
        # the critic's whole-token match never fires on a path term inside a real path ("/repo/tests/x.py"),
        # so terms containing '/' are matched as plain (case-insensitive) substrings of the added lines instead
        self.path_terms = sorted({t.lower() for t in terms if "/" in t and len(t) >= 4}, key=lambda t: (-len(t), t))
        self.critic = LeakageCritic(
            terms=[t for t in terms if "/" not in t],
            llm=self.llm,
            domain_brief=self.domain_brief,
            min_term_len=4,
        )

    @classmethod
    def for_domain(cls, domain, llm=None, split: str = "evolve") -> LeakageScreen:
        """Screen the terms of ``split``: the split the loop searches on (``Config.search_split``)."""
        return cls(forbidden=list(domain.leakage_terms(split)), llm=llm, domain_brief=domain.describe())

    def check(self, artifact: Artifact, base: Artifact | None = None) -> str | None:
        """Return a rejection reason or None."""
        self.n_screened += 1
        base = base or Artifact({})
        diff = base.diff(artifact)
        added = added_lines(diff).lower()
        hits = [t for t in self.path_terms if t in added]
        if hits:
            self.n_rejected += 1
            return f"leakage: edit hard-codes evaluation data: {hits[0]!r}"
        v = self.critic.screen(diff)
        if not v.accept:
            self.n_rejected += 1
            return f"leakage: {v.objections[0] if v.objections else v.stage}"
        # only tables the edit adds or grows count: a list the seed already had is not the candidate's leakage
        inherited = [tab for name, text in base.items() if name.endswith(".py") for tab in _string_tables(text)]
        for name, text in artifact.items():
            if name.endswith(".py"):
                n = max((len(t) for t in _string_tables(text) if _is_new_table(t, inherited)), default=0)
                if n > self.max_literal_strings:
                    self.n_rejected += 1
                    return f"leakage: {name} hard-codes a table of {n} string literals"
        return None


def _string_tables(src: str) -> list[list[str]]:
    """The string constants of each list/set/tuple literal and of each dict literal's keys."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            elts = node.keys
        elif isinstance(node, (ast.List, ast.Set, ast.Tuple)):
            elts = node.elts
        else:
            continue
        out.append([e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)])
    return out


def _is_new_table(table: list[str], inherited: list[list[str]]) -> bool:
    """True unless an inherited table supplied most of ``table``'s strings and is at least as long (not grown)."""
    if not table:
        return False
    mine = Counter(table)
    for old in inherited:
        shared = sum((mine & Counter(old)).values())
        if 2 * shared >= len(table) and len(table) <= len(old):
            return False
    return True

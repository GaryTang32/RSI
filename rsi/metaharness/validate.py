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
import functools
import itertools
import os
import sys
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional

from ..core.artifact import Artifact
from ..core.critic import LeakageCritic
from ..core import sandbox as _sandbox

try:   # the C function: it sets the mask before running pending handlers (signal's wrapper may run them first)
    from _signal import pthread_sigmask as _c_sigmask
except ImportError:  # pragma: no cover - platforms without it never fork here
    _c_sigmask = None

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
        buf = b""
        # Every signal is blocked in this thread across the fork, so the child gets none until its own handlers
        # are in place, inside its try, which always ends in os._exit: no handler of the parent's ever runs there.
        # In the parent a stop signal can be handled at almost any point (another thread may take it), so the pipe
        # and the child are stored with no gap after they are made, a stop handled inside an after-fork hook (where
        # Python would drop it) is kept and raised, and the cleanup is idempotent and retried until done.
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])   # the current mask; a pending signal raises here, first
        fds: list[int] = []
        pids: list[int] = []
        held: list[BaseException] = []
        done, err, deadline = False, None, 0.0
        inflight: dict[int, int] = {}
        try:
            signal.pthread_sigmask(signal.SIG_BLOCK, signal.valid_signals())
            # extend() stores what the call returns from C, before any bytecode could run a signal handler
            fds.extend(itertools.chain.from_iterable(itertools.starmap(os.pipe, [()])))
            prev_hook, hook = sys.unraisablehook, functools.partial(_hold_stop, held, sys.unraisablehook)
            sys.unraisablehook = hook
            try:
                pids.extend(itertools.starmap(os.fork, [()]))
            finally:
                sys.unraisablehook = prev_hook
            if pids[0] == 0:                                  # child
                try:
                    self._child(domain, artifact, llm, fds[0], fds[1], mask)
                finally:
                    os._exit(1)
            os.close(fds.pop())          # the write end (a signal between the pop and the close leaks it, at worst)
            _c_sigmask(signal.SIG_SETMASK, mask)   # sets the mask, then runs what is pending
            if held:
                raise held[0]
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
            # a second stop signal can land here too (Ctrl-C twice, or Ctrl-C then a kill): each step is idempotent,
            # so the cleanup is retried, with less grace for the child each time, until it completes; the first
            # such interruption is raised afterwards (no call before the try: nothing can run a handler there)
            first = None
            for grace in (2.0, 0.5, 0.0, 0.0, 0.0):
                try:
                    _cleanup_fork(fds, pids, mask, grace)
                    break
                except BaseException as e:  # noqa: BLE001
                    if first is None:
                        first = e
            if first is not None:
                raise first
        if not done:
            if time.monotonic() >= deadline:
                err = f"smoke timed out after {self.timeout_s}s"
                if inflight.get(0, 0) > 0:
                    err += f" ({inflight[0]} model call(s) in flight at the kill: their usage is unknown)"
            else:
                err = "smoke process died"
        return (err is None), (err or "OK")

    def _child(self, domain, artifact: Artifact, llm, r: int, w: int, mask) -> None:
        """The forked smoke child: run the smoke, stream usage back through ``w``, then leave through
        :func:`_child_exit` (never returns)."""
        import pickle
        import signal
        exiting = [False]

        def on_signal(signum, frame) -> None:
            # a SIGTERM (the parent's cleanup below, `kill`, `timeout`) or SIGINT ends the smoke through the
            # finally below, which closes the sandboxes it started; once the child is leaving it does nothing
            if not exiting[0]:
                raise (SystemExit(143) if signum == signal.SIGTERM else KeyboardInterrupt())

        try:
            os.close(r)
            signal.signal(signal.SIGTERM, on_signal)
            signal.signal(signal.SIGINT, on_signal)
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)   # a signal held since the fork arrives now
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
            # first, before any bytecode that runs pending handlers (a call, a loop's back-jump): from here on
            # no signal can make the child unwind into the parent's code
            exiting[0] = True
            try:
                _child_exit()
            finally:
                os._exit(0)


def _hold_stop(held: list, prev, unraisable) -> None:
    """``sys.unraisablehook`` while forking: a stop signal handled inside an after-fork hook raises there, where
    Python reports it as 'Exception ignored' and drops it. Keep it instead, to raise once the fork is done; pass
    anything else on. (A process another thread forks meanwhile inherits this hook: there it only keeps such an
    exception unprinted, since an unraisable one is never raised anyway.)"""
    if isinstance(unraisable.exc_value, (KeyboardInterrupt, SystemExit)):
        held.append(unraisable.exc_value)
    else:
        prev(unraisable)


def _cleanup_fork(fds: list, pids: list, mask, grace_s: float) -> None:
    """Restore the signal mask, close what is left of the pipe and stop the smoke child. Each step is idempotent,
    so a retry after an interruption does only what remains."""
    import signal
    _c_sigmask(signal.SIG_SETMASK, mask)
    while fds:
        os.close(fds.pop())
    if pids and pids[0] > 0:
        _stop_child(pids[0], grace_s)
        pids.clear()


def _child_exit() -> None:
    """Close a leaving forked smoke child's sandboxes (``sandbox.close_sandboxes``: kill the sandboxed processes it
    started, in whatever thread, and remove their scratch directories). Its handlers are no-ops by now and SIGTERM /
    SIGINT are ignored as well; anything that still interrupts the close makes it run again. The caller
    ``os._exit``s afterwards whatever happens here."""
    import signal
    for _ in range(100):
        try:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            _sandbox.close_sandboxes()
            return
        except BaseException:  # noqa: BLE001 - close again
            continue


def _stop_child(pid: int, grace_s: float = 2.0) -> None:
    """End a forked smoke child: SIGTERM (repeated), so it closes the sandboxes it started on its way out
    (:func:`_child_exit`); if it has not exited within ``grace_s``, kill its sandboxes from here, then SIGKILL it.
    It is signalled only while still unreaped (its pid cannot have been reused), so stopping it again after an
    interruption is safe."""
    import signal

    def reaped() -> bool:
        try:
            return bool(os.waitpid(pid, os.WNOHANG)[0])
        except ChildProcessError:                  # reaped already, by an earlier (interrupted) stop
            return True

    end, resend = time.monotonic() + grace_s, 0.0
    while not reaped():
        if time.monotonic() >= end:
            _sandbox.kill_child_sessions(pid)      # a child that never closed its sandboxes: kill them from here
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
            return
        if time.monotonic() >= resend:   # again now and then: one may have landed before the child could act on it
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

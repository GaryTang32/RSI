"""Proposers: who writes new harnesses from the experience store.

* :class:`AgentProposer` - the paper's proposer: a headless coding agent
  (``claude -p`` via :class:`rsi.core.AgentEditor`) whose working directory holds the
  whole history view read-only under ``_context/`` and which writes ``k`` complete
  new harnesses under ``agents/<name>/``, ``pending_eval.json`` and post-eval reports
  under ``reports/``. With ``prototype=True`` (default, as the release's Bash-enabled
  Claude Code) it may run ``python3`` and read-only shell commands to prototype; the
  ``--verbose`` transcript gives the files it actually opened (``files_read``).
* :class:`RewriteProposer` - one completion from any :class:`rsi.core.LLM`; the
  history view is *rendered* into the prompt (budgeted by file kind, so raw traces are
  always included) and the reply carries ``pending_eval`` JSON +
  ``=== FILE: agents/<name>/<path> ===`` blocks (and optional ``reports/`` blocks).
* :class:`rsi.metaharness.mock.MockProposer` - deterministic offline proposer.

The skill text adapts the release's ``SKILL.md`` (spec A3.3/A6) to any domain: you do
not run benchmarks; write exactly k candidates; change mechanisms, not parameters;
rotate exploitation axes; no dataset-specific hints; post-eval reports (Step 0);
prototype before implementing (Step 2); complete files only.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional, Sequence

from ..core.artifact import Artifact, parse_file_blocks
from ..core.editors import AgentEditor
from ..core.llm import LLM, ClaudeCLI, LLMResponse, Usage, estimate_tokens, extract_json, strip_attribution
from .store import safe_name

SKILL_TEXT = """\
# Meta-Harness (harness evolution) - run ONE iteration

You improve the HARNESS around a frozen model: the code that decides what information to store, what to
retrieve and how to present it to the model. **You do NOT run benchmarks.** A separate evaluator scores every
candidate you write and stores its code, scores and raw execution traces in the history directory.

## Constraints
- Write exactly {k} new candidate harnesses this iteration. Never write "the frontier is optimal" or stop early.
- Mix exploitation (improve a top system) and exploration (a different mechanism).
- Good candidates change a fundamental MECHANISM (a new retrieval algorithm, a new prompt architecture, a new
  learning strategy, a new memory structure). Parameter sweeps (pool sizes, counts, budgets) almost always tie or
  regress. If your logic is identical to the base except for numbers, rewrite it. Combining systems is valid.
- Exploitation axes: A = prompt template, B = memory / state content, C = selection / retrieval algorithm,
  D = sizing (how much is stored or shown), E = learning / update trigger, F = model usage inside the harness
  (e.g. model-written notes or lessons). Tag each candidate's components with its axis (e.g. "axis:C"). If the
  last 3 iterations explored the same axis, pick a different one.
- Anti-overfitting: no dataset-specific hints, never mention dataset or task names, never hard-code answers,
  inputs or ids from the traces. General patterns are fine. Would this help on MANY unfamiliar tasks?
- Candidates must keep the harness interface (see the domain description) and work from a cold start.

## Workflow
0. Post-eval reports: for each past iteration that has results in evolution_summary.jsonl but no report under
   reports/, write one (at most 30 lines): what changed, which units improved or regressed and why, and a
   takeaway for future iterations.
1. Read evolution_summary.jsonl and frontier_val.json, then the code of top candidates and, most importantly,
   their raw traces (failed AND successful examples) under candidates/<name>/eval/search/traces/.
2. Formulate {k} falsifiable hypotheses, each targeting a different mechanism.
3. {prototype}
4. Implement each candidate as COMPLETE files (copy a strong base, then change the mechanism). Self-critique:
   is this a genuinely new mechanism? If the logic is identical to the base except for numbers, rewrite it.
"""

#: Step 2 of the release skill when the proposer can run code (AgentProposer with ``prototype=True``)
PROTOTYPE_RUN = """Prototype (MANDATORY): for each candidate, write a small test script under `scratch/` that exercises the new
   retrieval / learning logic in isolation on real examples pulled from the traces, try 2-3 variants and compare
   them, and run it with `python3 scratch/<file>.py`. Everything outside agents/ and reports/ is discarded."""

#: Step 2 when the proposer cannot run code (RewriteProposer: one completion, no tools)
PROTOTYPE_ON_PAPER = """Prototype on paper (you cannot run code here): walk each mechanism through 2-3 real examples taken from the
   traces - what would it store, retrieve and show the model? - and compare variants before writing the code."""


def format_skill(skill: str, k: int, prototype: str) -> str:
    """Fill the skill template (custom skills may omit ``{prototype}``)."""
    return skill.format(k=k, prototype=prototype)


TASK_PROMPT = """\
Run iteration {iteration} of the evolution loop.

## Domain
{brief}

## Run directories
The full history of this run is under `{ctx}/` (read-only):
- `{ctx}/evolution_summary.jsonl` - past results (one row per candidate)
- `{ctx}/frontier_val.json` - Pareto frontier on the search set (score up, context cost down) and per-unit bests
- `{ctx}/candidates/<name>/src/` - every candidate's source; `.../eval/search/scores.json`, `.../traces/*.jsonl`
- `{ctx}/reports/` - post-eval reports (write NEW reports to `reports/iter<NNN>.md`, NNN = the iteration reported)
(Some of these may be absent: you see exactly what this run's history mode exposes.)

## Output
{output}
"""

AGENT_OUTPUT = """\
For each of the {k} candidates create a directory `agents/<new_name>/` containing the COMPLETE harness files
(same file layout as INSIDE the candidates' `src/` directories, e.g. `agents/<new_name>/harness.py`, not
`agents/<new_name>/src/harness.py`; unchanged files may be omitted and are copied from the base system). Then write `pending_eval.json`:
{{"iteration": {iteration}, "candidates": [{{"name": "<new_name>", "base_system": "<name of the system you started
from>", "hypothesis": "<falsifiable claim>", "axis": "exploitation|exploration", "components": ["<tags>"]}}]}}
Use new names (lowercase letters, digits, underscores) that do not exist yet. Write post-eval reports (Step 0) as
`reports/iter<NNN>.md`."""

REWRITE_OUTPUT = """\
Reply with:
1. A ```json fence holding {{"iteration": {iteration}, "candidates": [{{"name": "<new_name>", "base_system":
   "<system you started from>", "hypothesis": "<falsifiable claim>", "axis": "exploitation|exploration",
   "components": ["<tags>"]}}, ...]}} with exactly {k} candidates.
2. For every candidate, the COMPLETE content of each file you change:
=== FILE: agents/<new_name>/<path> ===
<entire file content>
<path> is relative to the harness root, exactly as the files appear inside a candidate's src/ directory
(e.g. agents/<new_name>/harness.py, NOT agents/<new_name>/src/harness.py).
Files you omit are copied from the candidate's base_system. Use new names (lowercase, digits, underscores).
3. Post-eval reports (Step 0), if any are missing, as
=== FILE: reports/iter<NNN>.md ===
<at most 30 lines>"""


@dataclass
class CandidateSpec:
    name: str
    artifact: Artifact
    hypothesis: str = ""
    axis: str = ""
    components: list[str] = field(default_factory=list)
    base_system: str = ""
    parents_read: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def pending_row(self) -> dict:
        return {"name": self.name, "base_system": self.base_system, "hypothesis": self.hypothesis,
                "axis": self.axis, "components": self.components}


@dataclass
class ProposalBatch:
    """One proposer call. ``files_read`` = history files whose content the proposal was based on (what a
    coding agent would open: for :class:`AgentProposer` the files its transcript shows it opened, for
    :class:`RewriteProposer` the files rendered into its prompt, for the mock the parents' code and the
    traces / summaries it diagnosed); ``files_scanned`` = files only parsed mechanically for bookkeeping
    (e.g. the mock reading every candidate's config to avoid repeats). ``reports`` = post-eval reports
    ``{"reports/<file>": text}`` (release Step 0)."""

    candidates: list[CandidateSpec] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    files_read: list[str] = field(default_factory=list)
    transcript: str = ""
    prompt: str = ""
    error: Optional[str] = None
    meta: dict = field(default_factory=dict)
    files_scanned: list[str] = field(default_factory=list)
    reports: dict[str, str] = field(default_factory=dict)


class Proposer:
    """Protocol: turn a history view into k candidate harnesses."""

    role = "proposer"

    def propose(self, *, iteration: int, view: dict[str, str], k: int, brief: str,
                artifacts: dict[str, Artifact], seed: int = 0) -> ProposalBatch:  # pragma: no cover
        """``view`` = what the proposer may read; ``artifacts`` = sources of visible systems
        (for copying unchanged files from ``base_system``)."""
        raise NotImplementedError


_FENCE_OPEN = re.compile(r"^\s*```[A-Za-z0-9_+.-]*[^\n]*\n")


def clean_code_block(text: str) -> str:
    """Unwrap a file body that is still inside a Markdown fence (``rsi.core.parse_file_blocks`` only
    unwraps it when nothing follows the closing fence) and drop prose after the closing fence."""
    if text is None:
        return text
    m = _FENCE_OPEN.match(text)
    if not m:
        return text
    body = text[m.end():]
    end = body.rfind("\n```")
    if end >= 0:
        body = body[:end + 1]
    elif body.rstrip().endswith("```"):
        body = body.rstrip()[:-3]
    return body.rstrip() + "\n"


def resolve_base(name: str, artifacts: dict[str, Artifact]) -> tuple[str, bool]:
    """``(system, fell_back)``: the visible system a candidate's ``base_system`` names (exact, then by safe name
    or case), else the first visible system with ``fell_back=True``."""
    name = str(name or "")
    if name in artifacts:
        return name, False
    if name:
        for n in artifacts:
            if safe_name(n) == safe_name(name) or n.lower() == name.lower():
                return n, False
    return (next(iter(artifacts)) if artifacts else ""), True


def reports_from(files: dict[str, str]) -> dict[str, str]:
    """Post-eval reports among a proposer's output files (``reports/<file>``; nested paths are flattened)."""
    out = {}
    for path, text in files.items():
        m = re.match(r"^reports/(.+)$", path)
        if m and text is not None and text.strip():
            out[f"reports/{safe_name(m.group(1).replace('/', '_'))}"] = clean_code_block(text)
    return out


def _collect(files: dict[str, str], header: dict, k: int, artifacts: dict[str, Artifact],
             taken: set[str], iteration: int) -> list[CandidateSpec]:
    """Group ``agents/<name>/<path>`` files into candidates, completing them from their base system.

    A ``base_system`` that names no visible system is NOT replaced silently (audit N10): the candidate is
    completed from the first visible system, and ``meta`` records ``base_fallback=True`` and the claimed name,
    which the store, the ledger and the audit trace keep."""
    groups: dict[str, dict[str, Optional[str]]] = {}
    for path, text in files.items():
        m = re.match(r"^agents/([^/]+)/(.+)$", path)
        if m:
            groups.setdefault(m.group(1), {})[m.group(2)] = clean_code_block(text)
    rows = header.get("candidates") if isinstance(header, dict) else None
    rows = rows if isinstance(rows, list) else []
    by_name = {str(r.get("name")): r for r in rows if isinstance(r, dict)}
    out = []
    for raw_name, updates in groups.items():
        row = by_name.get(raw_name, {})
        claimed = str(row.get("base_system") or "")
        base_name, fell_back = resolve_base(claimed, artifacts)
        base = artifacts.get(base_name) or Artifact({})
        # the history shows sources under candidates/<name>/src/<path>; a proposer that copies that layout
        # (agents/<name>/src/harness.py) would add dead files and leave the real harness unchanged
        # (observed live with haiku, validation/metaharness-solpi/mh_agentqa_live) -> map src/<path> to <path>
        if not any(p.startswith("src/") for p in base.files):
            updates = {(p[4:] if p.startswith("src/") else p): t for p, t in updates.items()}
        art = base.with_files({p: (None if (t or "").strip() == "<<DELETE>>" else t) for p, t in updates.items()})
        name = safe_name(raw_name)
        if name in taken:
            name = safe_name(f"{name}_i{iteration}")
        taken.add(name)
        comps = row.get("components") or []
        meta = {"base_fallback": True, "claimed_base_system": claimed or "(none)"} if fell_back else {}
        out.append(CandidateSpec(name, art, str(row.get("hypothesis", ""))[:2000], str(row.get("axis", "")),
                                 [str(c) for c in (comps if isinstance(comps, list) else [comps])], base_name,
                                 meta=meta))
        if len(out) >= k:
            break
    return out


class RewriteProposer(Proposer):
    """Single-completion proposer over a rendered history view (works with any LLM)."""

    def __init__(self, llm: LLM, *, max_context_chars: int = 60000, skill: str = SKILL_TEXT,
                 read_mix: Optional[dict] = None) -> None:
        self.llm = llm
        self.max_context_chars = max_context_chars
        self.skill = skill
        self.read_mix = read_mix

    def propose(self, *, iteration, view, k, brief, artifacts, seed=0):
        rendered, read = render_view(view, self.max_context_chars, mix=self.read_mix)
        prompt = TASK_PROMPT.format(iteration=iteration, brief=brief, ctx="history",
                                    output=REWRITE_OUTPUT.format(iteration=iteration, k=k))
        prompt = prompt + "\n\n## History (rendered)\n" + rendered
        system = format_skill(self.skill, k, PROTOTYPE_ON_PAPER)
        resp = self.llm.complete(prompt, system=system, seed=seed * 1000 + iteration, role=self.role)
        batch = ProposalBatch(usage=resp.usage, files_read=read, transcript=resp.text, prompt=prompt,
                              meta={"rendered_chars": len(rendered)})
        if not resp.ok:
            batch.error = f"llm error: {resp.error}"
            return batch
        header: dict = {}
        head_text = resp.text.split("=== FILE:")[0]
        try:
            h = extract_json(head_text)
            header = h if isinstance(h, dict) else {"candidates": h} if isinstance(h, list) else {}
        except ValueError:
            pass
        files = parse_file_blocks(resp.text)
        batch.reports = reports_from(files)
        batch.candidates = _collect(files, header, k, artifacts, set(), iteration)
        if not batch.candidates:
            batch.error = "no candidate files in reply"
        return batch


#: shell commands the prototyping agent may run (release: full Bash with --dangerously-skip-permissions)
PROTOTYPE_COMMANDS = ("python3", "python")
INSPECT_COMMANDS = ("cat", "head", "tail", "grep", "ls", "wc", "find", "sort", "uniq", "diff", "jq")


class AgentProposer(Proposer):
    """The paper's proposer: a coding agent over the history directory (``claude -p``).

    Uses :class:`rsi.core.AgentEditor`: the view is written read-only under ``_context/``; the agent may only
    write ``agents/*``, ``pending_eval.json`` and ``reports/*`` (post-eval reports, release Step 0).

    ``prototype=True`` (default, the release's behaviour): the agent also gets Bash, restricted by
    ``--allowedTools`` to ``python3``/``python`` and read-only inspection commands, so it can run the skill's
    mandatory prototype step (scripts under ``scratch/`` are discarded with everything else outside the
    editable paths). SAFETY: this executes model-written Python on the host with your permissions, as the
    release does (it even runs Claude Code with ``--dangerously-skip-permissions``). Use ``prototype=False``
    (no Bash at all, the earlier behaviour) or run inside a container when that is not acceptable.

    ``files_read`` is taken from the agent's ``--verbose`` transcript (files it opened with Read or with
    cat/head/tail/grep/sed in Bash), not the whole view; ``meta`` carries the tool calls, files searched with
    Grep/Glob and the prototype scripts it wrote."""

    def __init__(self, cli: ClaudeCLI, *, timeout_s: float = 2400.0, skill: str = SKILL_TEXT,
                 tools: Optional[tuple[str, ...]] = None, prototype: bool = True,
                 allowed_commands: Sequence[str] = PROTOTYPE_COMMANDS + INSPECT_COMMANDS) -> None:
        self.cli = cli
        self.skill = skill
        self.prototype = prototype
        if tools is None:
            tools = ("Read", "Edit", "Write", "Glob", "Grep") + (("Bash",) if prototype else ())
        allowed = [f"Bash({c} *)" for c in allowed_commands] if prototype and "Bash" in tools else []
        self.agent_cli = TranscriptCLI(cli, allowed_tools=allowed)
        self.editor = AgentEditor(self.agent_cli, tools=tools, timeout_s=timeout_s)

    def propose(self, *, iteration, view, k, brief, artifacts, seed=0):
        workspace = Artifact({"agents/README.md": "Write new candidates here: agents/<name>/<files>.\n",
                              "reports/README.md": "Write post-eval reports here: reports/iter<NNN>.md.\n"})
        prompt = TASK_PROMPT.format(iteration=iteration, brief=brief, ctx="_context",
                                    output=AGENT_OUTPUT.format(iteration=iteration, k=k))
        t0 = time.time()
        agent_cli = getattr(self, "agent_cli", None)
        if agent_cli is not None:
            agent_cli.reset()
        prop = self.editor.edit(workspace, prompt, context=view,
                                editable=["agents/*", "pending_eval.json", "reports/*"],
                                system=format_skill(self.skill, k, PROTOTYPE_RUN if getattr(self, "prototype", False)
                                                    else PROTOTYPE_ON_PAPER),
                                seed=seed, role=self.role)
        calls = tool_calls(agent_cli.last_messages) if agent_cli is not None else []
        read, searched = files_touched(calls, view)
        blocked = list(getattr(prop, "blocked_files", []) or [])
        batch = ProposalBatch(usage=prop.usage, transcript=prop.raw, prompt=prompt, files_read=read,
                              meta={"seconds": time.time() - t0, "files_searched": searched,
                                    "tool_summary": _tool_summary(calls),
                                    "tool_calls": [{"name": c["name"], "input": _short(c["input"])} for c in calls][:300],
                                    "prototype_files": sorted(b for b in blocked if b.startswith("scratch/")),
                                    "transcript_available": bool(agent_cli is not None and agent_cli.last_messages)})
        if prop.artifact is None:
            batch.error = prop.error or "agent produced no files"
            return batch
        if prop.error and prop.error.startswith("agent error"):
            # release: a failed or timed-out proposer (claude_wrapper returns ok=False, e.g. exit 124) skips the
            # iteration even if pending_eval.json was already written [spec A3.2]
            batch.error = prop.error
            return batch
        files = {n: prop.artifact[n] for n in prop.artifact if n not in ("agents/README.md", "reports/README.md")}
        header: dict = {}
        if "pending_eval.json" in files:
            try:
                header = json.loads(files.pop("pending_eval.json"))
            except json.JSONDecodeError:
                header = {}
        batch.reports = reports_from(files)
        batch.candidates = _collect(files, header, k, artifacts, set(), iteration)
        if not batch.candidates:
            batch.error = prop.error or "no candidates written under agents/"
        return batch


class TranscriptCLI:
    """A :class:`ClaudeCLI` adapter for :class:`rsi.core.AgentEditor` (which calls ``run_agent``):

    * adds ``--allowedTools`` patterns (the restricted Bash of the prototype step; in ``acceptEdits`` mode a
      command that matches no pattern is denied, since nobody answers permission prompts under ``-p``);
    * adds ``--verbose``, so ``--output-format json`` returns the whole message list, and keeps it in
      ``last_messages`` (the tool calls the agent made - the data the release's ``claude_wrapper`` logs as
      ``files_read``);
    * does not retry a timed-out session (release: exit 124 skips the iteration).

    Usage is metered on the wrapped CLI's meter, as ``ClaudeCLI.run_agent`` does."""

    def __init__(self, cli: ClaudeCLI, *, allowed_tools: Sequence[str] = ()) -> None:
        self.cli = cli
        self.allowed_tools = list(allowed_tools)
        self.last_messages: list = []

    @property
    def meter(self):
        return self.cli.meter

    @property
    def name(self) -> str:
        return self.cli.name

    def reset(self) -> None:
        self.last_messages = []

    def run_agent(self, prompt: str, *, cwd: str, system: Optional[str] = None,
                  tools: tuple[str, ...] = ("Read", "Edit", "Write", "Glob", "Grep"),
                  timeout_s: Optional[float] = None, role: str = "agent") -> LLMResponse:
        cmd = self.cli.base_cmd(system) + ["--verbose", "--tools", ",".join(tools), "--permission-mode", "acceptEdits",
                                           "--add-dir", cwd]
        if self.allowed_tools:
            cmd += ["--allowedTools", *self.allowed_tools]          # variadic: keep it last
        resp, messages = run_transcript(self.cli, cmd, prompt, cwd=cwd, timeout=timeout_s)
        self.last_messages = messages
        self.cli.meter.add(role, resp.usage)
        return resp


def run_transcript(cli: ClaudeCLI, cmd: list[str], prompt: str, *, cwd: Optional[str] = None,
                   timeout: Optional[float] = None) -> tuple[LLMResponse, list]:
    """Run a ``claude -p --output-format json --verbose`` command; return (response, message list).
    Mirrors ``ClaudeCLI._run`` (retries on malformed output / CLI errors) but keeps the transcript."""
    last_err = "unknown"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}          # prototype runs leave no __pycache__
    for attempt in range(max(1, cli.retries)):
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout or cli.timeout_s,
                                  cwd=cwd, env=env)
        except subprocess.TimeoutExpired:
            last_err = "timeout"
            break
        dt = time.time() - t0
        messages = _parse_messages(proc.stdout)
        result = next((m for m in reversed(messages) if isinstance(m, dict) and m.get("type") == "result"), None)
        if result is None and len(messages) == 1 and isinstance(messages[0], dict) and "result" in messages[0]:
            result = messages[0]
        if not isinstance(result, dict):
            last_err = (proc.stderr or proc.stdout or "no output")[-500:]
            time.sleep(min(2 ** attempt, 4))
            continue
        if result.get("is_error"):
            last_err = str(result.get("result") or result.get("subtype") or "is_error")[-500:]
            time.sleep(min(2 ** attempt, 4))
            continue
        u = result.get("usage") or {}
        in_tok = int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0)) + int(
            u.get("cache_creation_input_tokens", 0))
        usage = Usage(1, in_tok, int(u.get("output_tokens", 0)), float(result.get("total_cost_usd") or 0.0), dt)
        return LLMResponse(text=strip_attribution(result.get("result") or ""), usage=usage, model=cli.model,
                           raw=result), messages
    return LLMResponse(text="", usage=Usage(1, estimate_tokens(prompt), 0, 0.0, 0.0), model=cli.model,
                       error=last_err), []


def _parse_messages(stdout: str) -> list:
    """``--output-format json`` (+ ``--verbose``: a JSON list) or stream-json (one object per line)."""
    try:
        data = json.loads(stdout)
        return data if isinstance(data, list) else [data]
    except (json.JSONDecodeError, TypeError):
        out = []
        for line in (stdout or "").splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return out


def tool_calls(messages: list) -> list[dict]:
    """``[{name, input}]`` of every tool call in a Claude Code transcript."""
    out = []
    for m in messages or []:
        if not isinstance(m, dict) or m.get("type") != "assistant":
            continue
        content = (m.get("message") or {}).get("content") or []
        for b in content if isinstance(content, list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                out.append({"name": str(b.get("name")), "input": b.get("input") or {}})
    return out


_READ_CMDS = ("cat", "head", "tail", "grep", "sed", "awk", "less", "more", "wc", "jq", "diff")


def files_touched(calls: list[dict], view: dict[str, str]) -> tuple[list[str], list[str]]:
    """(files read, files/dirs searched) as history-view paths. Read = ``Read`` targets and files named in
    cat/head/tail/grep/sed/... commands; searched = ``Grep``/``Glob`` paths (directories included)."""
    def rel(p: str) -> Optional[str]:
        p = str(p or "").strip().strip("'\"")
        i = p.find("_context/")
        if i < 0:
            return None
        return p[i + len("_context/"):].rstrip("/")

    read, searched = [], []
    for c in calls:
        inp = c.get("input") or {}
        name = c.get("name")
        if name == "Read":
            r = rel(inp.get("file_path", ""))
            if r is not None and r in view:
                read.append(r)
        elif name in ("Grep", "Glob"):
            r = rel(inp.get("path", "")) or rel(inp.get("pattern", ""))
            if r is not None:
                searched.append(r)
        elif name == "Bash":
            try:
                toks = shlex.split(str(inp.get("command", "")))
            except ValueError:
                toks = str(inp.get("command", "")).split()
            if toks and os.path.basename(toks[0]) in _READ_CMDS:
                for t in toks[1:]:
                    r = rel(t)
                    if r is not None:
                        (read if r in view else searched).append(r)
    return sorted(set(read)), sorted(set(searched))


def _tool_summary(calls: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in calls:
        out[c["name"]] = out.get(c["name"], 0) + 1
    return out


def _short(inp: dict, n: int = 300) -> dict:
    return {k: (v[:n] + "...") if isinstance(v, str) and len(v) > n else v for k, v in (inp or {}).items()
            if k not in ("content", "new_string", "old_string")}


#: The paper's median proposer reading mix per iteration (App. A.1, Table 8: 41% code, 40% traces, 6% scores,
#: 13% other). :func:`render_view` splits its character budget this way, so raw traces are always rendered.
READ_MIX = {"code": 0.41, "traces": 0.40, "scores": 0.06, "other": 0.13}

_FAIL_LINE = re.compile(r'"ok":\s*false|"error"|Traceback|Incorrect|incorrect|FAIL|expected ', re.I)


def _view_kind(path: str) -> str:
    if not path.startswith("candidates/"):
        return "other"                        # run files, reports, session meta
    if "/src/" in path:
        return "code"
    if "/traces/" in path or "/per_task/" in path:
        return "traces"
    return "scores"                           # scores.json, meta.json, summary.md


def excerpt(text: str, cap: int, field_cap: int = 700) -> str:
    """At most ~``cap`` chars of a file. JSONL traces keep the first and last records plus as many
    failure-looking records (``"ok": false``, errors, tracebacks; latest first) as fit, each long string field
    cut in the middle; other text keeps its head and tail."""
    if len(text) <= cap:
        return text
    lines = text.splitlines()
    if lines and sum(1 for l in lines if l.lstrip().startswith("{")) >= 0.8 * len(lines):
        def shrink(line: str) -> str:
            if len(line) <= 2 * field_cap:
                return line
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                return line[:field_cap] + f" ...[{len(line) - 2 * field_cap} chars cut]... " + line[-field_cap:]

            def cut(v):
                if isinstance(v, str) and len(v) > field_cap:
                    h = field_cap // 2
                    return v[:h] + f" ...[{len(v) - 2 * h} chars cut]... " + v[-h:]
                if isinstance(v, list):
                    return [cut(x) for x in v]
                if isinstance(v, dict):
                    return {k: cut(x) for k, x in v.items()}
                return v
            return json.dumps({k: cut(v) for k, v in rec.items()})

        n = len(lines)
        # first/last records, then failures from the END backwards (the final evaluation phase comes last in a
        # trace), then everything else, also latest first
        order = [0, n - 1] + [i for i in range(n - 2, 0, -1) if _FAIL_LINE.search(lines[i])] + list(range(n - 2, 0, -1))
        keep, used = set(), 0
        for i in order:
            if i in keep:
                continue
            ln = shrink(lines[i])
            if used + len(ln) + 1 > cap and keep:
                continue
            keep.add(i)
            used += len(ln) + 1
            if used >= cap:
                break
        out, last = [], -1
        for i in sorted(keep):
            if i > last + 1:
                out.append(f"... [{i - last - 1} lines omitted] ...")
            out.append(shrink(lines[i]))
            last = i
        if last < n - 1:
            out.append(f"... [{n - 1 - last} lines omitted] ...")
        return "\n".join(out)
    h = cap // 3
    return text[:h] + f"\n...[{len(text) - cap} chars omitted]...\n" + text[-(cap - h):]


def render_view(view: dict[str, str], budget: int, per_file: int = 12000,
                mix: Optional[dict] = None) -> tuple[str, list[str]]:
    """Render a history view into prompt text under ``budget`` characters.

    The budget is split by file kind following the paper's median reading mix (:data:`READ_MIX`: code, raw
    traces, scores, other); budget one kind leaves unused flows to the others. Within a kind, candidates go by
    search score. Traces: all units of the best candidate first, then one unit per remaining candidate in
    turn; each trace is an :func:`excerpt` (first/last records + failures), widened up to ``per_file`` when
    budget is left over. Before this change every source
    and score outranked every trace, and on MemoClassify 0 of 42 trace files fit (audit N3). Returns
    ``(text, rendered paths)``."""
    mix = dict(mix or READ_MIX)

    def cand(path: str) -> Optional[str]:
        m = re.match(r"candidates/([^/]+)/", path)
        return m.group(1) if m else None

    def cand_score(name: Optional[str]) -> float:
        if name is None:
            return 0.0
        s = view.get(f"candidates/{name}/eval/search/scores.json")
        try:
            return float(json.loads(s)["score"]) if s else -1.0
        except (ValueError, KeyError, TypeError):
            return -1.0

    names = sorted({c for c in map(cand, view) if c}, key=lambda n: (-cand_score(n), n))
    rank = {n: i for i, n in enumerate(names)}
    run_first = ("evolution_summary.jsonl", "frontier_val.json")

    def unit_of(path: str) -> str:
        return path.rsplit("/", 1)[-1].rsplit(".", 1)[0]

    units_of: dict[Optional[str], list[str]] = {}
    for q in view:
        if _view_kind(q) == "traces":
            units_of.setdefault(cand(q), []).append(unit_of(q))
    units_of = {c: sorted(set(u)) for c, u in units_of.items()}

    def unit_index(path: str) -> int:                    # per_task/<u>.json and traces/<u>.jsonl share an index
        us = units_of.get(cand(path), [])
        return us.index(unit_of(path)) if unit_of(path) in us else 0

    def prio(path: str) -> tuple:
        k = _view_kind(path)
        if k == "other":
            return (run_first.index(path) if path in run_first else 2, path)
        r = rank.get(cand(path), len(rank))
        if k == "traces":
            per_task = 0 if "/per_task/" in path else 1
            return (0 if r < 1 else 1, 0 if r < 1 else unit_index(path), r, per_task, path)
        return (r, path)

    kinds = ("other", "code", "scores", "traces")
    by_kind = {k: sorted((p for p in view if _view_kind(p) == k), key=prio) for k in kinds}
    n_traces = max(1, len(by_kind["traces"]))
    trace_cap = min(per_file, max(1500, int(budget * mix.get("traces", 0)) // min(n_traces, 8)))
    chunks: dict[str, str] = {}
    used = 0

    def chunk_for(path: str, cap: int) -> str:
        text = view[path]
        if len(text) > cap:
            text = excerpt(text, cap) + f"\n...[excerpt: {len(view[path]):,} chars in full]"
        return f"=== HISTORY FILE: {path} ===\n{text}\n"

    def fill(paths, limit: int, cap_of) -> None:
        nonlocal used
        spent = 0
        for path in paths:
            if path in chunks:
                continue
            ch = chunk_for(path, cap_of(path))
            if spent + len(ch) > limit or used + len(ch) > budget:
                continue
            chunks[path] = ch
            spent += len(ch)
            used += len(ch)

    cap_of = lambda p: trace_cap if _view_kind(p) == "traces" else per_file   # noqa: E731
    for k in kinds:                                       # pass 1: each kind within its share
        fill(by_kind[k], int(budget * mix.get(k, 0)), cap_of)
    for k in kinds:                                       # pass 2: leftover budget, same priority order
        fill(by_kind[k], budget - used, cap_of)
    for path in by_kind["traces"]:                        # pass 3: widen trace excerpts with what is left
        if path not in chunks or len(view[path]) <= trace_cap:
            continue
        room = budget - used + len(chunks[path])
        cap = min(per_file, room - len(path) - 200)
        if cap <= trace_cap:
            break
        wider = chunk_for(path, cap)
        if len(wider) > len(chunks[path]) and used - len(chunks[path]) + len(wider) <= budget:
            used += len(wider) - len(chunks[path])
            chunks[path] = wider
    order = sorted(chunks, key=lambda p: ((0,) if _view_kind(p) == "other" else (1, rank.get(cand(p), len(rank)))) +
                   ((kinds.index(_view_kind(p)),) + prio(p)[-1:]))
    return "".join(chunks[p] for p in order), order


# ------------------------------------------------------------------ summarisers
SUMMARY_PROMPT = """Summarise the execution traces of one harness candidate in at most 5 sentences for an engineer
who will improve the harness: overall accuracy, recurring failure patterns, anything notable. Do not quote
individual examples.

{traces}"""


class LLMSummarizer:
    """Trace summaries for the ``scores_summary`` ablation (paper Table 3, row 2)."""

    def __init__(self, llm: LLM, max_chars: int = 40000) -> None:
        self.llm = llm
        self.max_chars = max_chars

    def __call__(self, traces: dict[str, str]) -> str:
        text = "\n".join(f"--- {u} ---\n{t}" for u, t in traces.items())[: self.max_chars]
        resp = self.llm.complete(SUMMARY_PROMPT.format(traces=text), role="summarizer", seed=0)
        return resp.text.strip() if resp.ok else "(summary unavailable)"


def generic_summary(traces: dict[str, str]) -> str:
    n = sum(len(t.splitlines()) for t in traces.values())
    return f"{len(traces)} evaluation units, {n} trace lines. No further detail."

"""E8 - The no-peeking constraint matters.

Claim [doc "Strategies must decide using only what they have revealed so far. They
can't peek at unrevealed scores or use known best answers"; paper App.B.2 hard
constraints]: without a prefix guard, a policy that peeks gets an inflated replay score,
wins selection, and then does not deliver online, where the future is not recorded. The
guard blocks it.

Two deliberately cheating policies and the honest adaptive policy are replayed over
recorded synthetic worlds with the guard ON (subprocess sandbox + prefix proxy) and OFF
(raw question, in-process), and run online on fresh worlds:

* ``oracle`` reads the hidden recorded tree (``question.tree``) and ``best_so_far``; the
  static check rejects it, the runtime guard disqualifies it;
* ``peek_reset`` uses only the public API - it passes the static check - but explores
  every cell, remembers the scores, calls ``question.reset()`` and walks straight to the
  remembered best. The guard treats a reset after the first probe as a violation.

Disqualified episodes score below every honest episode on the same world (quality floor
minus the cost of the whole grid minus 1). Raw Eq.1 (beta1=0.01, beta2=0.005) throughout.
With ``--llm claude:haiku`` one policy written by the LLM developer (Listing-2 prompt, fed
the honest policy's replay report) is added and checked by the same guard.

    python experiments/dream-rsi/e8_no_peeking.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
import numpy as np
from _common import developer_of, fmt, parse_args, pmap, save, summ

from rsi.dream import (Config, DevContext, DreamRSILoop, Eq1Objective, GridPlan, OnlineQuestion, ReplayEvaluator,
                       SubprocessRunner, VersionRecord, adaptive, code_of, parallel_refine, static_check,
                       template_code)
from rsi.dream.guard import InProcessSession, SubprocessSession
from rsi.dream.objectives import EpisodeResult
from rsi.domains.discovery import SyntheticConfig, SyntheticDomain

W, GRID = 4, (6, 4)
OBJ = Eq1Objective(normalize=False)
CHEATS = ("oracle", "peek_reset")


def record(seed):
    dom = SyntheticDomain(SyntheticConfig(seed=seed))
    cfg = Config(rounds=1, W=W, branch_count=GRID[0], refine_count=GRID[1], dream=False, sandbox="inprocess",
                 seed=seed, agent_workers=1)
    return DreamRSILoop(dom.as_task(), dom.mock_agent(), config=cfg).run().meta["worlds"][0]


def online(job):
    """One live search on a fresh world; ``guarded=False`` hands the policy the raw question.
    LLM-written code always runs in the subprocess sandbox (guarded)."""
    name, code, seed, guarded, sandboxed = job
    dom = SyntheticDomain(SyntheticConfig(seed=seed))
    task, agent = dom.as_task(), dom.mock_agent()
    root = task.seed_artifact()
    q = OnlineQuestion(task=task, agent=agent, root_artifact=root, root_eval=task.evaluate(root), W=W,
                       plan=GridPlan(*GRID), workers=1, seed=seed, round_index=1)
    if sandboxed:
        with SubprocessSession(code, timeout_s=30) as sess:
            out = sess.solve({}, q)
    else:
        out = InProcessSession(code).solve({}, q, unguarded=not guarded)
    st = q.stats()
    ep = EpisodeResult("online", None, {}, {}, False, N=st["N"], k=st["k"], batch_sizes=st["batch_sizes"],
                       best=st["best"], root=float(st["baseline"]), ceiling=float(st["best"]),
                       world_size=GRID[0] * (GRID[1] + 1), W=W, error=out.error, violations=out.violations,
                       batch_errors=out.batch_errors)
    return name, guarded, OBJ.score_episode(ep), st["N"], ep.disqualified


def llm_policy(spec, worlds):
    """One Listing-2 revision of the honest adaptive policy, written by the LLM developer."""
    ev = ReplayEvaluator(OBJ, W=W, fallback=GRID, runner=SubprocessRunner(timeout_s=30), root_mode="addressable")
    inc = VersionRecord(0, code_of(adaptive()), label="incumbent")
    inc.report = ev.evaluate(inc.code, worlds)
    rev = developer_of(spec).revise(DevContext(1, [inc], [], [], code_of(parallel_refine()), "eq1", W,
                                               first_in_phase=True), seed=0)
    return rev


def main():
    a = parse_args("E8 no-peeking guard", default_seeds=8)
    n_online = 10 if a.quick else 40
    worlds = [record(300 + i) for i in range(a.seeds)]
    pol = {"honest_adaptive": code_of(adaptive()), "oracle": template_code("oracle"),
           "peek_reset": template_code("peek_reset")}
    llm_note = None
    if a.llm != "sim":
        rev = llm_policy(a.llm, worlds)
        llm_note = {"ok": rev.ok, "error": rev.error, "change": rev.change[:300]}
        if rev.ok:
            pol["llm_written"] = rev.code
    lint = {n: static_check(c) for n, c in pol.items()}
    on = ReplayEvaluator(OBJ, W=W, fallback=GRID, runner=SubprocessRunner(timeout_s=30), root_mode="addressable")
    off = ReplayEvaluator(OBJ, W=W, fallback=GRID, runner="inprocess", root_mode="addressable", unguarded=True)
    rep = {}
    for n, c in pol.items():
        r_on = on.evaluate(c, worlds)
        r_off = off.evaluate(c, worlds) if n != "llm_written" else r_on   # never run LLM code unguarded
        rep[n] = {"static_check_ok": lint[n].ok, "static_check_errors": lint[n].errors,
                  "replay_guard_on": r_on.value, "replay_guard_off": r_off.value,
                  "disqualified_on": r_on.disqualified, "violations_on": r_on.diagnostics.get("violations"),
                  "N_off": float(np.mean([e.N for e in r_off.episodes])),
                  "attainment_off": float(np.mean([e.attainment for e in r_off.episodes]))}
    jobs = [(n, c, 2000 + s, g, n == "llm_written") for n, c in pol.items() for s in range(n_online)
            for g in ((True,) if n == "llm_written" else (False, True))]
    res_on = {}
    for n, g, v, N, disq in pmap(online, jobs, a.workers):
        res_on.setdefault((n, g), []).append((v, N, disq))
    for n in pol:
        for g in (False, True):
            vals = res_on.get((n, g)) or res_on[(n, True)]
            tag = "guarded" if g else "unguarded"
            rep[n][f"online_{tag}"] = summ([v for v, _, _ in vals])
            rep[n][f"online_N_{tag}"] = float(np.mean([N for _, N, _ in vals]))
            rep[n][f"online_disqualified_{tag}"] = int(sum(d for _, _, d in vals))
    pick_off = max(pol, key=lambda n: rep[n]["replay_guard_off"])
    pick_on = max(pol, key=lambda n: rep[n]["replay_guard_on"])
    for n, r in rep.items():
        r["replay_minus_online_guard_off"] = r["replay_guard_off"] - r["online_unguarded"]["mean"]
        print(f"{n:<16} static ok={r['static_check_ok']} | replay guard OFF {r['replay_guard_off']:+.4f} "
              f"(N={r['N_off']:.1f}, attainment {r['attainment_off']:.2f}) | guard ON {r['replay_guard_on']:+.4f} "
              f"(disqualified {r['disqualified_on']}/{a.seeds}) | online {fmt(r['online_unguarded'])}")
    print(f"selection without guard picks {pick_off}; with guard picks {pick_on}")
    h = rep["honest_adaptive"]
    parts, ok_all = [], True
    for n in CHEATS:
        r = rep[n]
        inflated = r["replay_guard_off"] > h["replay_guard_off"] and \
            r["replay_minus_online_guard_off"] > h["replay_minus_online_guard_off"] + 0.05
        blocked = r["disqualified_on"] == a.seeds and r["replay_guard_on"] < h["replay_guard_on"]
        ok_all &= inflated and blocked
        parts.append(
            f"{n} (static check {'passes' if r['static_check_ok'] else 'rejects it'}): unguarded replay "
            f"{r['replay_guard_off']:+.3f} vs honest {h['replay_guard_off']:+.3f}, i.e. inflated by "
            f"{r['replay_minus_online_guard_off']:+.3f} over its online value (honest "
            f"{h['replay_minus_online_guard_off']:+.3f}); online {r['online_unguarded']['mean']:+.3f} vs honest "
            f"{h['online_unguarded']['mean']:+.3f} ("
            + ("the replay advantage does not survive online" if r["online_unguarded"]["mean"] <
               h["online_unguarded"]["mean"] else "its online fallback happens to be competitive, so the damage "
               "is a mis-estimated value, not an online collapse")
            + f"); the guard disqualifies it in {r['disqualified_on']}/{a.seeds} worlds")
    verdict = (f"{'REPRODUCED' if ok_all and pick_on == 'honest_adaptive' else 'PARTIAL'}: without the guard "
               f"selection picks {pick_off}; with it, {pick_on}. " + "; ".join(parts))
    if "llm_written" in rep:
        r = rep["llm_written"]
        verdict += (f"; LLM-written policy: static check {'ok' if r['static_check_ok'] else 'failed'}, "
                    f"disqualified in {r['disqualified_on']}/{a.seeds} replay worlds, violations {r['violations_on']}")
    print(verdict)
    save("e8_no_peeking", {"config": {"worlds": a.seeds, "online_worlds": n_online, "W": W, "grid": GRID,
                                      "objective": "raw Eq.1 beta1=0.01 beta2=0.005; disqualified = root - "
                                                   "beta1*|grid| - 1", "root_mode": "addressable",
                                      "llm": a.llm if a.llm != "sim" else "not used", "llm_revision": llm_note},
                           "results": rep, "pick_without_guard": pick_off, "pick_with_guard": pick_on,
                           "verdict": verdict}, a.out)


if __name__ == "__main__":
    main()

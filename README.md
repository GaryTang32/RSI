# RSI recorded output

Everything the experiments, validation runs and live demo recorded, moved off `main` to keep the code branch small.
Paths match `main`: `results/<method>/`, `validation/<method>/`, `demo/runs/` and `demo/*.json`.

Restore the files into a `main` checkout (they are git-ignored there):

```bash
git fetch origin data
git archive origin/data results validation demo | tar -x
```

Snapshot of `main` at commit 6bf6b9f; the full history of every file is still in `main`'s log.

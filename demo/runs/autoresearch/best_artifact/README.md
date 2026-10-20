# tinylm autoresearch

A CPU analogue of karpathy/autoresearch: give an agent a small but real language-model
training setup and let it experiment autonomously. It modifies the code, trains for a fixed
budget, checks if the result improved, keeps or discards, and repeats.

## How it works

The repo is deliberately small and only has two files that matter:

- **`prepare.py`** - fixed constants, one-time data prep (a byte corpus built from the Python
  standard library's docstrings: train and val splits), and runtime utilities (dataloader,
  budget clock, evaluation). Not modified.
- **`train.py`** - the single file the agent edits. Contains the model (a numpy byte-level MLP
  language model: CONTEXT previous bytes -> embeddings -> DEPTH hidden layers -> 256 logits),
  the optimizer (Adam or SGD with momentum) and the training loop. Everything is fair game:
  architecture, hyperparameters, optimizer, batch size, etc.

By design, training runs for a **fixed budget** (wall clock of training, the first 11 steps
excluded), regardless of what the code does. The metric is **val_bpb** (validation bits per
byte) - lower is better, and vocabulary-size independent. The model is byte-level, so every
token is exactly one byte.

There is no GPU: everything runs in numpy on a shared multi-core CPU, and budgets are seconds,
not minutes. Wall-clock runs are noisy (a few hundredths of a bit per byte between identical
runs). Peak memory is the process's peak resident set size (the VRAM analogue); about 0.1 GB of
it is the Python interpreter and numpy.

## Project structure

```
prepare.py  - constants, data, dataloader + budget clock, evaluate_bpb (do not modify)
train.py    - model, optimizer, training loop (the agent modifies this)
README.md   - this file
```

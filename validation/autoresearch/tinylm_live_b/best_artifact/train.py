"""train.py - the file the research agent edits (tinylm autoresearch task).

A byte-level MLP language model in plain numpy (Bengio-style / makemore): the
previous CONTEXT bytes are embedded, concatenated, passed through DEPTH hidden
layers and projected to 256 next-byte logits. Everything here is fair game:
architecture, optimizer, hyperparameters, schedules, batch size, model size.

The script trains for the fixed budget from prepare.py (wall-clock seconds by
default; the first 11 steps are not counted), then prints a summary block that
starts with ``val_bpb:``. Run it as ``python train.py``.
"""
import math
import time

import numpy as np

import prepare

t_start = time.time()

# ---------------------------------------------------------------------------
# Hyperparameters (edit these)
# ---------------------------------------------------------------------------
CONTEXT = 6              # bytes of history the model sees
EMBED_DIM = 16           # byte embedding size
HIDDEN = 128             # width of each hidden layer
DEPTH = 1                # number of hidden layers
ACTIVATION = "tanh"      # tanh | relu
BATCH_SIZE = 8           # rows per step
TRAIN_SEQ_LEN = 16       # positions per row (examples per step = BATCH_SIZE * TRAIN_SEQ_LEN)
OPTIMIZER = "adam"       # adam | sgd
LR = 0.007
ADAM_BETAS = (0.9, 0.99)
WEIGHT_DECAY = 0.0
WARMUP_RATIO = 0.0
WARMDOWN_RATIO = 0.5
FINAL_LR_FRAC = 0.0
INIT_SCALE = 1.0
EVAL_BATCH_SIZE = 64
SEED = 42


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
class MLPLM:
    def __init__(self, rng):
        C, E, H = CONTEXT, EMBED_DIM, HIDDEN
        p = {"wte": rng.normal(0.0, 1.0, (256, E)) * INIT_SCALE}
        fan_in = C * E
        for i in range(DEPTH):
            p[f"w{i}"] = rng.normal(0.0, 1.0, (fan_in, H)) * INIT_SCALE / math.sqrt(fan_in)
            p[f"b{i}"] = np.zeros(H)
            fan_in = H
        p["w_out"] = rng.normal(0.0, 1.0, (fan_in, 256)) * INIT_SCALE * 0.1 / math.sqrt(fan_in)
        p["b_out"] = np.zeros(256)
        self.p = p

    def _contexts(self, x):
        B, T = x.shape
        xp = np.concatenate([np.zeros((B, CONTEXT - 1), dtype=x.dtype), x], axis=1)
        idx = np.arange(T)[:, None] + np.arange(CONTEXT)[None, :]
        return xp[:, idx].reshape(B * T, CONTEXT)          # previous CONTEXT bytes, current last

    def _act(self, z):
        return np.tanh(z) if ACTIVATION == "tanh" else np.maximum(z, 0.0)

    def _forward(self, x):
        ctx = self._contexts(x)
        h = self.p["wte"][ctx].reshape(len(ctx), -1)
        cache = [(ctx, h)]
        for i in range(DEPTH):
            z = h @ self.p[f"w{i}"] + self.p[f"b{i}"]
            h = self._act(z)
            cache.append((z, h))
        return h @ self.p["w_out"] + self.p["b_out"], cache

    def logits(self, x):
        out, _ = self._forward(x)
        return out.reshape(x.shape + (256,))

    def forward(self, x, y, reduction="mean"):
        out, _ = self._forward(x)
        out = out - out.max(axis=1, keepdims=True)
        logp = out - np.log(np.exp(out).sum(axis=1, keepdims=True))
        nll = -logp[np.arange(len(out)), y.reshape(-1).astype(np.int64)]
        return nll if reduction == "none" else float(nll.mean())

    def loss_and_grads(self, x, y):
        out, cache = self._forward(x)
        n = len(out)
        out = out - out.max(axis=1, keepdims=True)
        e = np.exp(out)
        probs = e / e.sum(axis=1, keepdims=True)
        yt = y.reshape(-1).astype(np.int64)
        with np.errstate(divide="ignore"):
            loss = float(-np.log(probs[np.arange(n), yt]).mean())
        d = probs
        d[np.arange(n), yt] -= 1.0
        d /= n
        g = {}
        h_last = cache[-1][1]
        g["w_out"] = h_last.T @ d
        g["b_out"] = d.sum(0)
        dh = d @ self.p["w_out"].T
        for i in reversed(range(DEPTH)):
            z, h = cache[i + 1]
            dz = dh * (1.0 - h * h) if ACTIVATION == "tanh" else dh * (z > 0)
            h_prev = cache[i][1]
            g[f"w{i}"] = h_prev.T @ dz
            g[f"b{i}"] = dz.sum(0)
            dh = dz @ self.p[f"w{i}"].T
        ctx = cache[0][0]
        g_wte = np.zeros_like(self.p["wte"])
        np.add.at(g_wte, ctx.reshape(-1), dh.reshape(-1, EMBED_DIM))
        g["wte"] = g_wte
        return loss, g


def lr_multiplier(progress):
    if progress < WARMUP_RATIO:
        return progress / WARMUP_RATIO if WARMUP_RATIO > 0 else 1.0
    if progress < 1.0 - WARMDOWN_RATIO:
        return 1.0
    cooldown = (1.0 - progress) / WARMDOWN_RATIO
    return cooldown * 1.0 + (1 - cooldown) * FINAL_LR_FRAC


# ---------------------------------------------------------------------------
# Training loop (fixed budget)
# ---------------------------------------------------------------------------
rng = np.random.default_rng([SEED, prepare.RUN_SEED])
model = MLPLM(rng)
num_params = sum(v.size for v in model.p.values())
m_state = {k: np.zeros_like(v) for k, v in model.p.items()}
v_state = {k: np.zeros_like(v) for k, v in model.p.items()}
train_loader = prepare.make_dataloader(BATCH_SIZE, TRAIN_SEQ_LEN, "train")

total_training_time = 0.0     # seconds (wallclock budget) or bytes (tokens budget)
step = 0
smooth_loss = 0.0
t_train_start = time.time()
for x, y in train_loader:
    t0 = time.time()
    progress = min(total_training_time / prepare.TIME_BUDGET, 1.0)
    lr = LR * lr_multiplier(progress)
    loss, grads = model.loss_and_grads(x, y)
    if math.isnan(loss) or loss > 100:
        print("FAIL")
        raise SystemExit(1)
    b1, b2 = ADAM_BETAS
    for k, g in grads.items():
        if WEIGHT_DECAY and k.startswith("w"):
            g = g + WEIGHT_DECAY * model.p[k]
        if OPTIMIZER == "adam":
            m_state[k] = b1 * m_state[k] + (1 - b1) * g
            v_state[k] = b2 * v_state[k] + (1 - b2) * g * g
            mhat = m_state[k] / (1 - b1 ** (step + 1))
            vhat = v_state[k] / (1 - b2 ** (step + 1))
            model.p[k] -= lr * mhat / (np.sqrt(vhat) + 1e-8)
        else:
            m_state[k] = 0.9 * m_state[k] + g
            model.p[k] -= lr * m_state[k]
    smooth_loss = 0.9 * smooth_loss + 0.1 * loss
    dt = time.time() - t0
    if step > 10:
        total_training_time += dt if prepare.BUDGET_KIND == "wallclock" else x.size
    step += 1
    if step > 10 and total_training_time >= prepare.TIME_BUDGET:
        break

training_seconds = time.time() - t_train_start
val_bpb = prepare.evaluate_bpb(model, EVAL_BATCH_SIZE)

print("---")
print(f"val_bpb:          {val_bpb:.6f}")
print(f"training_seconds: {training_seconds:.1f}")
print(f"total_seconds:    {time.time() - t_start:.1f}")
print(f"peak_mem_mb:      {prepare.peak_mem_mb():.1f}")
print(f"num_steps:        {step}")
print(f"num_params_M:     {num_params / 1e6:.3f}")
print(f"depth:            {DEPTH}")

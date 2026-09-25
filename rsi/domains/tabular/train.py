"""train.py - the file the research agent edits (tabular autoresearch task).

Feature engineering lives in ``featurize(frame)`` (per-row features only, plus
lookups fitted on ``train`` at module level); the model is scikit-learn's
HistGradientBoostingClassifier. The locked protocol in prepare.py scores it by
5-fold CV ROC AUC (higher is better) and prints ``cv_auc:``.
"""
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

import prepare

# ---------------------------------------------------------------------------
# Hyperparameters (edit these)
# ---------------------------------------------------------------------------
MAX_ITER = 30
LEARNING_RATE = 0.1
MAX_LEAF_NODES = 31
MAX_DEPTH = 6
MIN_SAMPLES_LEAF = 20
L2_REGULARIZATION = 0.0
MAX_BINS = 255
SEED = 42

FEATURES = ["hour", "dow", "origin", "carrier", "distance", "load", "temp", "rain"]

train = prepare.load_train()


def featurize(frame):
    cols = [np.asarray(frame[c], dtype=float) for c in FEATURES]
    return np.column_stack(cols)


def make_model():
    return HistGradientBoostingClassifier(
        max_iter=MAX_ITER, learning_rate=LEARNING_RATE, max_leaf_nodes=MAX_LEAF_NODES, max_depth=MAX_DEPTH,
        min_samples_leaf=MIN_SAMPLES_LEAF, l2_regularization=L2_REGULARIZATION, max_bins=MAX_BINS,
        early_stopping=False, random_state=SEED,
    )


cv_auc, cv_std = prepare.cross_val_auc(make_model, featurize, train)
model = make_model().fit(featurize(prepare.inputs(train)), train[prepare.TARGET])
prepare.finish(model, featurize, cv_auc, cv_std)

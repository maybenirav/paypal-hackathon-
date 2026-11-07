"""Bayesian layer: Beta-Binomial posteriors, credible intervals, P(best), Thompson allocation.

Monte Carlo draws come from a seeded NumPy generator, so results repeat in tests and demos.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np

SAMPLES = 10_000
MIN_SHARE = 0.05    # every live option keeps this share of traffic, so none starves


@dataclass(frozen=True)
class Posterior:
    alpha: float
    beta: float

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)


def posterior(successes: int, trials: int, prior_a: float = 1.0, prior_b: float = 1.0) -> Posterior:
    if trials < 0 or not 0 <= successes <= trials:
        raise ValueError("need 0 <= successes <= trials")
    return Posterior(prior_a + successes, prior_b + trials - successes)


def draws(p: Posterior, rng: np.random.Generator, n: int = SAMPLES) -> np.ndarray:
    return rng.beta(p.alpha, p.beta, n)


def interval(samples: np.ndarray, level: float = 0.95) -> tuple[float, float]:
    tail = (1 - level) / 2
    lo, hi = np.quantile(samples, [tail, 1 - tail])
    return float(lo), float(hi)


def credible_interval(p: Posterior, level: float = 0.95, seed: int = 0,
                      samples: int = SAMPLES) -> tuple[float, float]:
    return interval(draws(p, np.random.default_rng(seed), samples), level)


def best_shares(samples: dict[str, np.ndarray]) -> dict[str, float]:
    """For each key, the share of draws in which it has the highest value."""
    keys = list(samples)
    if not keys:
        return {}
    wins = np.bincount(np.vstack([samples[k] for k in keys]).argmax(axis=0), minlength=len(keys))
    total = wins.sum()
    return {k: float(wins[i] / total) for i, k in enumerate(keys)}


def prob_best(posts: dict[str, Posterior], seed: int = 0, samples: int = SAMPLES) -> dict[str, float]:
    """P(arm has the highest true rate) under the posteriors."""
    rng = np.random.default_rng(seed)
    return best_shares({k: draws(p, rng, samples) for k, p in posts.items()})


def prob_beats(arm: Posterior, base: Posterior, seed: int = 0, samples: int = SAMPLES) -> float:
    """P(arm's true rate > baseline's true rate)."""
    rng = np.random.default_rng(seed)
    return float((draws(arm, rng, samples) > draws(base, rng, samples)).mean())


def shares_with_floor(best: dict[str, float], min_share: float = MIN_SHARE,
                      protected: str | None = None, protected_share: float = 0.0) -> dict[str, float]:
    """Traffic share = P(best), floored so no arm starves, then rescaled to sum to 1.

    `protected` keeps at least `protected_share`. Every comparison is made against the baseline, so
    starving it would make all of them less certain, and it limits exposure to unproven prices.
    """
    if not best:
        return {}
    floor = min(min_share, 1 / len(best))
    raw = {k: max(v, floor) for k, v in best.items()}
    total = sum(raw.values())
    shares = {k: v / total for k, v in raw.items()}
    if protected in shares and len(shares) > 1 and shares[protected] < protected_share:
        others = 1 - shares[protected]
        scale = (1 - protected_share) / others
        shares = {k: protected_share if k == protected else v * scale for k, v in shares.items()}
    return shares


def thompson_allocation(posts: dict[str, Posterior], min_share: float = MIN_SHARE,
                        seed: int = 0) -> dict[str, float]:
    return shares_with_floor(prob_best(posts, seed), min_share)


def assign_arm(weights: dict[str, float], rng: random.Random) -> str:
    """Draw one arm for a new sign-up according to the allocation weights."""
    r, acc = rng.random(), 0.0
    for k, w in weights.items():
        acc += w
        if r <= acc:
            return k
    return next(reversed(weights))

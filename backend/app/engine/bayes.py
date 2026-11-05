"""Bayesian layer: Beta-Binomial posteriors, credible intervals, P(best), Thompson allocation.

Deterministic (seeded) Monte Carlo so results are repeatable in tests and demos.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

SAMPLES = 4000


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


def _draws(p: Posterior, rng: random.Random, n: int) -> list[float]:
    return [rng.betavariate(p.alpha, p.beta) for _ in range(n)]


def credible_interval(p: Posterior, level: float = 0.95, seed: int = 0,
                      samples: int = SAMPLES) -> tuple[float, float]:
    xs = sorted(_draws(p, random.Random(seed), samples))
    lo = xs[int(((1 - level) / 2) * samples)]
    hi = xs[min(samples - 1, int((1 - (1 - level) / 2) * samples))]
    return lo, hi


def prob_best(posts: dict[str, Posterior], seed: int = 0, samples: int = SAMPLES) -> dict[str, float]:
    """P(arm has the highest true rate) under the posteriors."""
    rng = random.Random(seed)
    wins = {k: 0 for k in posts}
    for _ in range(samples):
        draw = {k: rng.betavariate(p.alpha, p.beta) for k, p in posts.items()}
        wins[max(draw, key=draw.get)] += 1
    return {k: v / samples for k, v in wins.items()}


def prob_beats(arm: Posterior, base: Posterior, seed: int = 0, samples: int = SAMPLES) -> float:
    """P(arm's true rate > baseline's true rate)."""
    rng = random.Random(seed)
    return sum(rng.betavariate(arm.alpha, arm.beta) > rng.betavariate(base.alpha, base.beta)
               for _ in range(samples)) / samples


def thompson_allocation(posts: dict[str, Posterior], min_share: float = 0.05,
                        seed: int = 0) -> dict[str, float]:
    """Traffic share per arm = P(best), floored at min_share so no arm starves (exploration)."""
    pb = prob_best(posts, seed)
    n = len(posts)
    floor = min(min_share, 1 / n)
    raw = {k: max(v, floor) for k, v in pb.items()}
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}


def assign_arm(weights: dict[str, float], rng: random.Random) -> str:
    """Draw one arm for a new sign-up according to the allocation weights."""
    r, acc = rng.random(), 0.0
    for k, w in weights.items():
        acc += w
        if r <= acc:
            return k
    return next(reversed(weights))

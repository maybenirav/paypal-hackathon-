"""Bayesian layer: Beta-Binomial posteriors, credible intervals, P(best), Thompson allocation.

The decision metric is new MRR per visitor = paid conversion x monthly-equivalent price, so a
cheaper arm can't "win" just by converting more. `scale` carries the price per arm: each
posterior draw of the paid-conversion rate is multiplied by it before arms are compared.

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


def prob_best(posts: dict[str, Posterior], seed: int = 0, samples: int = SAMPLES,
              scale: dict[str, float] | None = None) -> dict[str, float]:
    """P(arm has the highest true rate x scale) under the posteriors."""
    rng = random.Random(seed)
    sc = scale or {}
    wins = {k: 0 for k in posts}
    for _ in range(samples):
        draw = {k: rng.betavariate(p.alpha, p.beta) * sc.get(k, 1.0) for k, p in posts.items()}
        wins[max(draw, key=draw.get)] += 1
    return {k: v / samples for k, v in wins.items()}


def prob_beats(arm: Posterior, base: Posterior, seed: int = 0, samples: int = SAMPLES,
               arm_scale: float = 1.0, base_scale: float = 1.0) -> float:
    """P(arm's true rate x arm_scale > baseline's true rate x base_scale)."""
    rng = random.Random(seed)
    return sum(rng.betavariate(arm.alpha, arm.beta) * arm_scale
               > rng.betavariate(base.alpha, base.beta) * base_scale
               for _ in range(samples)) / samples


def thompson_allocation(posts: dict[str, Posterior], min_share: float = 0.05,
                        seed: int = 0, scale: dict[str, float] | None = None) -> dict[str, float]:
    """Traffic share per arm = P(best), floored at min_share so no arm starves (exploration).

    The reward is observed at checkout (paid conversion x price), so the bandit learns from
    every sign-up instead of waiting months for lifetime value.
    """
    pb = prob_best(posts, seed, scale=scale)
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

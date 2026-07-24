"""Content-free, session-scoped query budget for server-only resources.

This module is deliberately independent of Streamlit and other serving
frameworks.  Its serializable state contains only monotonic timestamps,
budget credits, and counters.  Source text, filenames, lexical items, and
per-document token counts are never accepted by or retained in the guard.

The guard limits one browser session only.  It is not a substitute for an
infrastructure-level IP/account/global rate limiter shared by every worker.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
import time
from typing import Any, Mapping


STATE_VERSION = 1


@dataclass(frozen=True, slots=True)
class QueryGuardConfig:
    """Policy for a monotonic token bucket and failed-attempt cooldown.

    One credit represents one document submitted for server-only lookup, with
    a minimum charge per request so repeated one-document probes cannot use the
    full capacity as individual queries.  Operators still need a shared limiter
    at the ingress layer because a new browser session starts a new bucket.
    """

    capacity: int = 200
    minimum_request_cost: int = 20
    refill_seconds_per_credit: float = 30.0
    failure_threshold: int = 3
    failure_cooldown_seconds: float = 120.0

    def __post_init__(self) -> None:
        for name in ("capacity", "minimum_request_cost", "failure_threshold"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.minimum_request_cost > self.capacity:
            raise ValueError("minimum_request_cost must not exceed capacity")
        for name in ("refill_seconds_per_credit", "failure_cooldown_seconds"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")


@dataclass(frozen=True, slots=True)
class QueryGuardState:
    """Serializable content-free state for one browser session."""

    credits: float
    updated_at: float
    blocked_until: float = 0.0
    attempts: int = 0
    successes: int = 0
    consecutive_failures: int = 0
    short_rejections: int = 0

    def to_mapping(self) -> dict[str, int | float]:
        """Return only the allow-listed, content-free state fields."""

        return {
            "version": STATE_VERSION,
            "credits": self.credits,
            "updated_at": self.updated_at,
            "blocked_until": self.blocked_until,
            "attempts": self.attempts,
            "successes": self.successes,
            "consecutive_failures": self.consecutive_failures,
            "short_rejections": self.short_rejections,
        }


@dataclass(frozen=True, slots=True)
class QueryGuardDecision:
    """Authorization result, including Retry-After-equivalent information."""

    allowed: bool
    state: QueryGuardState
    retry_after_seconds: int
    reason: str | None = None


class QueryBudgetExceeded(RuntimeError):
    """Raised by a serving adapter when a session budget denies a request."""

    def __init__(self, retry_after_seconds: int, reason: str) -> None:
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        self.reason = str(reason)
        super().__init__(
            f"Server-only query budget unavailable; retry after "
            f"{self.retry_after_seconds} seconds."
        )


def _now(value: float | None) -> float:
    current = time.monotonic() if value is None else float(value)
    if not math.isfinite(current) or current < 0:
        raise ValueError("now must be a non-negative finite monotonic timestamp")
    return current


def new_state(
    config: QueryGuardConfig | None = None,
    *,
    now: float | None = None,
) -> QueryGuardState:
    """Create a full bucket without accepting any user-derived content."""

    policy = config or QueryGuardConfig()
    current = _now(now)
    return QueryGuardState(credits=float(policy.capacity), updated_at=current)


def state_from_mapping(
    value: Mapping[str, Any] | None,
    config: QueryGuardConfig | None = None,
    *,
    now: float | None = None,
) -> QueryGuardState:
    """Restore and sanitize state across framework reruns.

    Unknown keys are discarded.  Invalid or version-mismatched state starts a
    new bucket; numeric values from valid state are clamped to safe ranges.
    """

    policy = config or QueryGuardConfig()
    current = _now(now)
    if not isinstance(value, Mapping) or value.get("version") != STATE_VERSION:
        return new_state(policy, now=current)
    try:
        credits = float(value["credits"])
        updated_at = float(value["updated_at"])
        blocked_until = float(value.get("blocked_until", 0.0))
        counters = {
            name: int(value.get(name, 0))
            for name in (
                "attempts",
                "successes",
                "consecutive_failures",
                "short_rejections",
            )
        }
    except (KeyError, TypeError, ValueError, OverflowError):
        return new_state(policy, now=current)
    numeric_values = (credits, updated_at, blocked_until)
    if not all(math.isfinite(item) and item >= 0 for item in numeric_values):
        return new_state(policy, now=current)
    if any(item < 0 for item in counters.values()):
        return new_state(policy, now=current)
    return QueryGuardState(
        credits=min(float(policy.capacity), credits),
        # Preserve a future timestamp so a monotonic-clock rollback cannot mint
        # credits until the clock catches up.
        updated_at=updated_at,
        blocked_until=blocked_until,
        attempts=counters["attempts"],
        successes=min(counters["successes"], counters["attempts"]),
        consecutive_failures=min(
            counters["consecutive_failures"], counters["attempts"]
        ),
        short_rejections=counters["short_rejections"],
    )


def _refill(
    state: QueryGuardState,
    config: QueryGuardConfig,
    current: float,
) -> QueryGuardState:
    # ``max`` makes a monotonic-clock rollback fail closed: no refill occurs.
    elapsed = max(0.0, current - state.updated_at)
    credits = min(
        float(config.capacity),
        state.credits + elapsed / config.refill_seconds_per_credit,
    )
    return replace(state, credits=credits, updated_at=max(state.updated_at, current))


def authorize(
    state: QueryGuardState,
    document_count: int,
    config: QueryGuardConfig | None = None,
    *,
    now: float | None = None,
) -> QueryGuardDecision:
    """Consume request/document credits or return an integer Retry-After duration."""

    policy = config or QueryGuardConfig()
    if (
        isinstance(document_count, bool)
        or not isinstance(document_count, int)
        or document_count <= 0
    ):
        raise ValueError("document_count must be a positive integer")
    if document_count > policy.capacity:
        raise ValueError("document_count exceeds the session query-budget capacity")
    cost = max(document_count, policy.minimum_request_cost)
    current = _now(now)
    refilled = _refill(state, policy, current)
    if refilled.blocked_until > current:
        retry_after = max(1, math.ceil(refilled.blocked_until - current))
        return QueryGuardDecision(
            allowed=False,
            state=refilled,
            retry_after_seconds=retry_after,
            reason="consecutive-failure cooldown",
        )
    if refilled.credits + 1e-12 < cost:
        missing = cost - refilled.credits
        wait_seconds = missing * policy.refill_seconds_per_credit
        retry_after = max(
            1,
            # Avoid overstating an exact integral wait because of binary float
            # noise (for example, 0.1 * 10 becoming 1.0000000000000009).
            math.ceil(max(0.0, wait_seconds - 1e-9)),
        )
        return QueryGuardDecision(
            allowed=False,
            state=refilled,
            retry_after_seconds=retry_after,
            reason="session document budget",
        )
    consumed = replace(
        refilled,
        credits=max(0.0, refilled.credits - cost),
        attempts=refilled.attempts + 1,
    )
    return QueryGuardDecision(
        allowed=True,
        state=consumed,
        retry_after_seconds=0,
    )


def record_outcome(
    state: QueryGuardState,
    *,
    success: bool,
    short_rejections: int = 0,
    config: QueryGuardConfig | None = None,
    now: float | None = None,
) -> QueryGuardState:
    """Record a content-free outcome after an authorized attempt.

    ``short_rejections`` is a count only.  The rejected text, its lexical items,
    and its length are intentionally outside this API.
    """

    policy = config or QueryGuardConfig()
    if (
        isinstance(short_rejections, bool)
        or not isinstance(short_rejections, int)
        or short_rejections < 0
    ):
        raise ValueError("short_rejections must be a non-negative integer")
    current = _now(now)
    updated = _refill(state, policy, current)
    total_short = updated.short_rejections + short_rejections
    if success:
        return replace(
            updated,
            successes=updated.successes + 1,
            consecutive_failures=0,
            short_rejections=total_short,
        )
    consecutive = updated.consecutive_failures + 1
    blocked_until = updated.blocked_until
    if consecutive >= policy.failure_threshold:
        blocked_until = max(
            blocked_until,
            current + policy.failure_cooldown_seconds,
        )
    return replace(
        updated,
        consecutive_failures=consecutive,
        short_rejections=total_short,
        blocked_until=blocked_until,
    )

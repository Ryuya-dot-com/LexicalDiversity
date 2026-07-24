import math

import pytest

from ldfreq.query_guard import (
    QueryBudgetExceeded,
    QueryGuardConfig,
    authorize,
    new_state,
    record_outcome,
    state_from_mapping,
)


def _policy(**overrides):
    values = {
        "capacity": 5,
        "minimum_request_cost": 1,
        "refill_seconds_per_credit": 10.0,
        "failure_threshold": 3,
        "failure_cooldown_seconds": 60.0,
    }
    values.update(overrides)
    return QueryGuardConfig(**values)


def test_document_cost_is_consumed_and_refilled_with_monotonic_time():
    policy = _policy()
    initial = new_state(policy, now=100.0)

    first = authorize(initial, 3, policy, now=100.0)
    assert first.allowed
    assert first.state.credits == 2
    assert first.state.attempts == 1

    second = authorize(first.state, 3, policy, now=109.0)
    assert not second.allowed
    assert second.retry_after_seconds == 1
    assert second.reason == "session document budget"

    third = authorize(second.state, 3, policy, now=110.0)
    assert third.allowed
    assert third.state.credits == 0


def test_denied_request_reports_ceil_retry_after_without_counting_attempt():
    policy = _policy()
    decision = authorize(new_state(policy, now=0), 5, policy, now=0)
    denied = authorize(decision.state, 1, policy, now=0.01)

    assert not denied.allowed
    assert denied.retry_after_seconds == 10
    assert denied.state.attempts == 1


def test_minimum_request_charge_limits_repeated_single_document_probes():
    policy = QueryGuardConfig(
        capacity=200,
        minimum_request_cost=20,
        refill_seconds_per_credit=30,
    )
    state = new_state(policy, now=0)
    for _ in range(10):
        decision = authorize(state, 1, policy, now=0)
        assert decision.allowed
        state = record_outcome(decision.state, success=True, config=policy, now=0)

    denied = authorize(state, 1, policy, now=0)
    assert not denied.allowed
    assert denied.retry_after_seconds == 600
    assert state.credits == 0


def test_failure_and_short_rejection_counts_trigger_cooldown():
    policy = _policy()
    state = new_state(policy, now=10)
    for timestamp in (10, 11, 12):
        decision = authorize(state, 1, policy, now=timestamp)
        assert decision.allowed
        state = record_outcome(
            decision.state,
            success=False,
            short_rejections=1,
            config=policy,
            now=timestamp,
        )

    assert state.consecutive_failures == 3
    assert state.short_rejections == 3
    blocked = authorize(state, 1, policy, now=20)
    assert not blocked.allowed
    assert blocked.reason == "consecutive-failure cooldown"
    assert blocked.retry_after_seconds == 52


def test_success_resets_only_the_consecutive_failure_counter():
    policy = _policy(failure_threshold=4)
    state = new_state(policy, now=0)
    first = authorize(state, 1, policy, now=0)
    state = record_outcome(
        first.state,
        success=False,
        short_rejections=2,
        config=policy,
        now=0,
    )
    second = authorize(state, 1, policy, now=1)
    state = record_outcome(second.state, success=True, config=policy, now=1)

    assert state.attempts == 2
    assert state.successes == 1
    assert state.consecutive_failures == 0
    assert state.short_rejections == 2


def test_serialized_state_discards_unknown_or_content_fields():
    policy = _policy()
    restored = state_from_mapping(
        {
            "version": 1,
            "credits": 4,
            "updated_at": 3,
            "attempts": 1,
            "successes": 0,
            "consecutive_failures": 1,
            "short_rejections": 1,
            "source_text": "must disappear",
            "filename": "learner.txt",
            "lexical_items": ["secret"],
        },
        policy,
        now=4,
    )
    serialized = restored.to_mapping()

    assert set(serialized) == {
        "version",
        "credits",
        "updated_at",
        "blocked_until",
        "attempts",
        "successes",
        "consecutive_failures",
        "short_rejections",
    }
    assert "must disappear" not in repr(serialized)
    assert "learner.txt" not in repr(serialized)
    assert "secret" not in repr(serialized)


def test_clock_rollback_does_not_refill_credits():
    policy = _policy()
    spent = authorize(new_state(policy, now=100), 5, policy, now=100).state
    restored = state_from_mapping(spent.to_mapping(), policy, now=90)
    rolled_back = authorize(restored, 1, policy, now=90)

    assert not rolled_back.allowed
    assert rolled_back.state.credits == 0
    assert rolled_back.state.updated_at == 100
    assert rolled_back.retry_after_seconds == 10


@pytest.mark.parametrize("cost", [0, -1, True, 6])
def test_invalid_or_unfulfillable_cost_is_rejected(cost):
    policy = _policy()
    with pytest.raises(ValueError):
        authorize(new_state(policy, now=0), cost, policy, now=0)


def test_policy_and_timestamp_validation():
    with pytest.raises(ValueError):
        QueryGuardConfig(capacity=0)
    with pytest.raises(ValueError):
        QueryGuardConfig(refill_seconds_per_credit=math.inf)
    with pytest.raises(ValueError):
        QueryGuardConfig(capacity=10, minimum_request_cost=11)
    with pytest.raises(ValueError):
        new_state(now=-1)


def test_serving_exception_contains_only_retry_metadata():
    error = QueryBudgetExceeded(7, "session document budget")

    assert error.retry_after_seconds == 7
    assert error.reason == "session document budget"
    assert "retry after 7 seconds" in str(error)

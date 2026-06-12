"""Priority queue tests — ordering, starvation guard, visibility timeout.

All tests use fakeredis so no real Redis is needed.
The sorted-set logic (scores, zpopmin) runs against a real in-memory implementation,
not against mocks — this catches actual ordering bugs.
"""
import time

import pytest
from unittest.mock import patch

from gateway.api.models import Priority
from gateway.config import settings
from gateway.queue.priority_queue import (
    CLAIMED_KEY,
    QUEUE_KEY,
    _OFFSETS,
    ack,
    dequeue,
    enqueue,
    promote_starved,
    requeue_timed_out,
)


@pytest.fixture(autouse=True)
def use_fake_redis(fake_redis):
    with patch("gateway.queue.priority_queue._get_redis", return_value=fake_redis):
        yield


async def test_high_priority_dequeues_before_normal():
    """A high item added after a normal one should still come out first."""
    await enqueue("normal-job", Priority.normal)
    await enqueue("high-job", Priority.high)

    assert await dequeue() == "high-job"
    assert await dequeue() == "normal-job"


async def test_fifo_within_same_priority():
    """Within a band, items dequeue in the order they were enqueued."""
    await enqueue("job-1", Priority.normal)
    await enqueue("job-2", Priority.normal)
    await enqueue("job-3", Priority.normal)

    assert [await dequeue(), await dequeue(), await dequeue()] == [
        "job-1", "job-2", "job-3"
    ]


async def test_empty_queue_returns_none():
    assert await dequeue() is None


async def test_ack_removes_from_claimed(fake_redis):
    await enqueue("job-x", Priority.normal)
    pid = await dequeue()
    await ack(pid)

    # Nothing left to re-queue.
    assert await requeue_timed_out() == 0


async def test_visibility_timeout_requeues_claimed(fake_redis):
    """If a worker claims a job and doesn't ack before the deadline, it goes back."""
    await enqueue("timeout-job", Priority.normal)
    await dequeue()  # Moves to claimed with a future deadline.

    # Overwrite the deadline to a past timestamp to simulate a timeout.
    await fake_redis.zadd(CLAIMED_KEY, {"timeout-job": time.time() - 1})

    assert await requeue_timed_out() == 1
    assert await dequeue() == "timeout-job"


async def test_low_priority_promoted_after_starvation_wait(fake_redis):
    """A low-priority item waiting past starvation_max_wait_seconds gets bumped to normal."""
    old_score = _OFFSETS[Priority.low] + (
        time.time() - settings.starvation_max_wait_seconds - 10
    )
    await fake_redis.zadd(QUEUE_KEY, {"starving-job": old_score})

    assert await promote_starved() == 1
    assert await dequeue() == "starving-job"


async def test_priority_ordering_all_three_bands():
    """All three priority bands always dequeue in the right order."""
    await enqueue("low", Priority.low)
    await enqueue("normal", Priority.normal)
    await enqueue("high", Priority.high)

    assert await dequeue() == "high"
    assert await dequeue() == "normal"
    assert await dequeue() == "low"

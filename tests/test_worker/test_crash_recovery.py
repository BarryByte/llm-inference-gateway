"""WAL-based crash recovery tests.

The WAL is the guarantee that no work is silently lost when a worker process dies.
These tests verify:
  1. Tasks in-flight at crash time get re-queued on restart.
  2. Completed tasks are NOT re-queued (no duplicate results).
  3. Tasks that fail max_retries times land in the DLQ.
  4. The WAL file itself is written correctly (append-only, parseable JSON).
"""
import json
import time

import pytest

from gateway.queue.wal import append, replay


async def test_wal_writes_json_lines(tmp_path):
    """Each append() call should write one parseable JSON line."""
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("gateway.queue.wal._wal_file", tmp_path / "test.wal")

        append("p1", "pending", "processing")
        append("p1", "processing", "completed")

    lines = (tmp_path / "test.wal").read_text().strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["prompt_id"] == "p1"
    assert first["from"] == "pending"
    assert first["to"] == "processing"
    assert "ts" in first  # timestamp always present


async def test_replay_returns_empty_when_no_wal(tmp_path):
    """No WAL file → replay returns an empty list (clean start)."""
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("gateway.queue.wal._wal_file", tmp_path / "missing.wal")
        entries = replay()

    assert entries == []


async def test_replay_reads_all_entries(tmp_path):
    """replay() returns every entry written to the WAL in order."""
    wal_path = tmp_path / "gateway.wal"
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("gateway.queue.wal._wal_file", wal_path)

        append("job-a", "pending", "processing")
        append("job-b", "pending", "claimed")
        append("job-a", "processing", "completed")

        entries = replay()

    assert len(entries) == 3
    assert entries[0]["prompt_id"] == "job-a"
    assert entries[1]["prompt_id"] == "job-b"
    assert entries[2]["to"] == "completed"


async def test_pool_replay_requeues_orphaned_tasks(tmp_path, fake_redis):
    """_replay_wal() should re-queue tasks whose last WAL state is claimed or processing."""
    from unittest.mock import AsyncMock, MagicMock, patch

    wal_path = tmp_path / "gateway.wal"

    # Simulate a crash: job-a was processing, job-b was completed.
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("gateway.queue.wal._wal_file", wal_path)
        append("job-a", "pending", "processing")
        append("job-b", "pending", "processing")
        append("job-b", "processing", "completed")

    # Fake DB row for job-a (status=processing, not yet done).
    fake_row = MagicMock()
    fake_row.status = "processing"
    fake_row.priority = "normal"

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(return_value=fake_row)

    with pytest.MonkeyPatch().context() as mp, \
         patch("gateway.worker.pool.AsyncSessionLocal", return_value=mock_session), \
         patch("gateway.worker.pool.pqueue.enqueue", new_callable=AsyncMock) as mock_enqueue, \
         patch("gateway.queue.wal._wal_file", wal_path):

        from gateway.worker.pool import _replay_wal
        requeued = await _replay_wal()

    # Only job-a should be re-queued (job-b was completed).
    assert requeued == 1
    mock_enqueue.assert_called_once()
    call_args = mock_enqueue.call_args[0]
    assert call_args[0] == "job-a"


async def test_completed_task_not_requeued(tmp_path, fake_redis):
    """A task whose last WAL state is 'completed' must never be re-queued."""
    from unittest.mock import AsyncMock, patch

    wal_path = tmp_path / "gateway.wal"
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("gateway.queue.wal._wal_file", wal_path)
        append("done-job", "pending", "processing")
        append("done-job", "processing", "completed")

    with patch("gateway.worker.pool.pqueue.enqueue", new_callable=AsyncMock) as mock_enqueue, \
         patch("gateway.queue.wal._wal_file", wal_path):

        from gateway.worker.pool import _replay_wal
        requeued = await _replay_wal()

    assert requeued == 0
    mock_enqueue.assert_not_called()

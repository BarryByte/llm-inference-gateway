"""Worker pool — spawns N async workers that pull from the priority queue in a loop.

Startup sequence:
  1. Replay WAL to find tasks that were in-flight when the process last died.
  2. Re-queue those orphaned tasks so they get a fresh attempt.
  3. Launch N worker coroutines that run forever.

Back-pressure: the /process route checks queue depth before accepting new work.
If depth > MAX_QUEUE_DEPTH, the API returns 503 — workers never see the overflow.
"""
import asyncio

from gateway.api.models import Priority
from gateway.config import settings
from gateway.queue import priority_queue as pqueue
from gateway.queue.wal import replay as wal_replay
from gateway.storage.db import AsyncSessionLocal
from gateway.storage.models import Prompt
from gateway.worker.processor import process


async def run_worker(worker_id: int) -> None:
    """Single worker loop: dequeue → process → repeat."""
    while True:
        # Periodically recover stuck claimed items (e.g. worker died mid-task).
        await pqueue.requeue_timed_out()
        # Bump low-priority items that have been waiting too long.
        await pqueue.promote_starved()

        prompt_id = await pqueue.dequeue()
        if prompt_id:
            await process(prompt_id)
        else:
            # Empty queue — sleep briefly to avoid busy-spinning.
            await asyncio.sleep(0.1)


async def _replay_wal() -> int:
    """
    On startup, scan the WAL and re-queue any task that was mid-flight.

    A task is "orphaned" if its last WAL state is claimed or processing,
    meaning the process died before it could finish or ack.
    Tasks already marked completed or failed are left alone.
    """
    entries = wal_replay()

    # Walk forward — last entry per prompt_id wins.
    final_states: dict[str, str] = {}
    for entry in entries:
        final_states[entry["prompt_id"]] = entry["to"]

    requeued = 0
    async with AsyncSessionLocal() as session:
        for prompt_id, last_state in final_states.items():
            if last_state not in ("claimed", "processing"):
                continue
            row = await session.get(Prompt, prompt_id)
            if row and row.status not in ("completed", "failed"):
                await pqueue.enqueue(prompt_id, Priority[row.priority])
                requeued += 1

    return requeued


async def start_pool() -> None:
    """Replay WAL, then start all worker coroutines."""
    count = await _replay_wal()
    if count:
        print(f"[pool] WAL replay: re-queued {count} orphaned task(s)")

    await asyncio.gather(*[run_worker(i) for i in range(settings.worker_count)])

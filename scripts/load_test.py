"""Load test — hammers POST /process and prints throughput + error rate."""
import asyncio
import time
import httpx

GATEWAY_URL = "http://localhost:8000"
CONCURRENCY = 50
TOTAL_REQUESTS = 500


async def send(client: httpx.AsyncClient, i: int) -> dict:
    try:
        r = await client.post(
            f"{GATEWAY_URL}/process",
            json={
                "user_id": f"u{i % 10}",
                "prompt_id": f"load-{i}",
                "text": f"Load test prompt number {i}",
                "priority": "normal",
            },
            timeout=30,
        )
        return {"status": r.status_code}
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def main() -> None:
    sem = asyncio.Semaphore(CONCURRENCY)
    results = []

    async def bounded(i: int) -> None:
        async with sem:
            results.append(await send(client, i))

    start = time.perf_counter()
    async with httpx.AsyncClient() as client:
        await asyncio.gather(*[bounded(i) for i in range(TOTAL_REQUESTS)])
    elapsed = time.perf_counter() - start

    ok = sum(1 for r in results if r.get("status") == 200)
    errors = TOTAL_REQUESTS - ok
    print(f"Requests : {TOTAL_REQUESTS}")
    print(f"Completed: {ok}  Errors: {errors}")
    print(f"Elapsed  : {elapsed:.2f}s")
    print(f"RPS      : {TOTAL_REQUESTS / elapsed:.1f}")


if __name__ == "__main__":
    asyncio.run(main())

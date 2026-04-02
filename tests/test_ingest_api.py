import asyncio
import httpx
from main import app


async def main():
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            with open("test_recibo.pdf", "rb") as f:
                response = await ac.post(
                    "/api/v1/ingest/upload",
                    files={"file": ("test_recibo.pdf", f, "application/pdf")},
                )
            print(f"Status: {response.status_code}")
            print(f"JSON: {response.json()}")
            ingest_id = response.json().get("ingest_id")

            # poll the ingest endpoint
            for _ in range(30):
                await asyncio.sleep(3)
                res_get = await ac.get(f"/api/v1/ingest/{ingest_id}")
                print(f"Poll Status: {res_get.status_code}")
                # Print only status field
                print(f"Poll JSON: {res_get.json().get('status')}")
                if res_get.json().get("status") in ["completed", "failed", "error"]:
                    print("Result:")
                    print(res_get.json())
                    break
    except Exception:
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())

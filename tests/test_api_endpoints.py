#!/usr/bin/env python
"""
End-to-End test for the API endpoints with the agent.
Tests the full flow: Server startup → PDF upload → Agent processing → Response validation

Usage:
    python test_api_endpoints.py
"""

import os
import sys
import time
import asyncio
import subprocess
from pathlib import Path
import pytest
from reportlab.pdfgen import canvas
import httpx

pytestmark = pytest.mark.skip(reason="Manual E2E script; requires running server")

# Load env
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))


def create_test_pdf(filename: str = "test_recibo.pdf") -> str:
    """Create a simple test receipt PDF."""

    pdf_path = Path(filename)
    c = canvas.Canvas(str(pdf_path))

    # Header
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, 750, "RECIBO DE PAGO")

    # Content
    c.setFont("Helvetica", 11)
    y = 700
    lines = [
        "Fecha: 18 de febrero de 2026",
        "Banco: Bancolombia S.A.",
        "Concepto: Transacción y servicios bancarios",
        "Beneficiario: Empresa XYZ SAS",
        "Monto: $150.000",
        "Referencia: REF-2026-001234",
        "Tipo: Recibo de pago",
    ]

    for line in lines:
        c.drawString(50, y, line)
        y -= 30

    c.showPage()
    c.save()

    return str(pdf_path)


async def test_health_endpoint():
    """Test the health check endpoint."""
    print("\n" + "=" * 60)
    print("TEST 1: Health Check Endpoint")
    print("=" * 60)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get("http://localhost:8000/health")

            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "healthy":
                    print("✅ Health check passed")
                    print(f"   Response: {data}")
                    return True
                else:
                    print(f"❌ Unexpected health response: {data}")
                    return False
            else:
                print(f"❌ Health endpoint returned {response.status_code}")
                return False

    except Exception as e:
        print(f"❌ Health check failed: {str(e)}")
        return False


async def test_ingest_upload_valid_pdf():
    """Test uploading a valid PDF to ingest endpoint."""
    print("\n" + "=" * 60)
    print("TEST 2: Ingest Upload - Valid PDF")
    print("=" * 60)

    pdf_path = create_test_pdf("test_valid.pdf")
    print(f"Created test PDF: {pdf_path}")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            with open(pdf_path, "rb") as f:
                files = {"file": ("test_recibo.pdf", f, "application/pdf")}
                response = await client.post(
                    "http://localhost:8000/api/v1/ingest/upload", files=files
                )

            print(f"Response status: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                print("✅ Upload successful (200 OK)")
                print(f"   Message: {data.get('message')}")
                print(f"   Ingest ID: {data.get('ingest_id')}")
                print(f"   Status: {data.get('status')}")

                # Validate structure
                required_fields = ["message", "ingest_id", "status"]
                if all(field in data for field in required_fields):
                    print("✅ Response has all required fields")
                    return True
                else:
                    print(f"❌ Missing fields. Got: {data.keys()}")
                    return False
            else:
                print(f"❌ Upload failed with status {response.status_code}")
                print(f"   Response: {response.text}")
                return False

    except Exception as e:
        print(f"❌ Upload test failed: {str(e)}")
        return False
    finally:
        Path(pdf_path).unlink(missing_ok=True)


async def test_ingest_upload_invalid_file():
    """Test uploading a non-PDF file (should fail)."""
    print("\n" + "=" * 60)
    print("TEST 3: Ingest Upload - Invalid File Type")
    print("=" * 60)

    # Create a text file
    txt_file = "test_invalid.txt"
    with open(txt_file, "w") as f:
        f.write("This is not a PDF")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            with open(txt_file, "rb") as f:
                files = {"file": ("test_invalid.txt", f, "text/plain")}
                response = await client.post(
                    "http://localhost:8000/api/v1/ingest/upload", files=files
                )

            print(f"Response status: {response.status_code}")

            if response.status_code == 400:
                print("✅ Correctly rejected non-PDF (400 Bad Request)")
                print(f"   Error: {response.json().get('detail')}")
                return True
            else:
                print(f"❌ Expected 400, got {response.status_code}")
                return False

    except Exception as e:
        print(f"❌ Invalid file test failed: {str(e)}")
        return False
    finally:
        Path(txt_file).unlink(missing_ok=True)


async def test_ingest_upload_missing_file():
    """Test uploading with missing file (should fail)."""
    print("\n" + "=" * 60)
    print("TEST 4: Ingest Upload - Missing File")
    print("=" * 60)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Don't attach any file
            response = await client.post("http://localhost:8000/api/v1/ingest/upload")

            print(f"Response status: {response.status_code}")

            if response.status_code in [400, 422]:
                print(f"✅ Correctly rejected missing file ({response.status_code})")
                return True
            else:
                print(f"⚠️  Unexpected status {response.status_code}")
                return False

    except Exception as e:
        print(f"❌ Missing file test failed: {str(e)}")
        return False


async def start_server():
    """Start the FastAPI server in background."""
    print("Starting FastAPI server...")

    # Start server as subprocess
    process = subprocess.Popen(
        ["uv", "run", "python", "main.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for server to start (max 10 seconds)
    max_retries = 20
    for i in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=1) as client:
                response = await client.get("http://localhost:8000/health")
                if response.status_code == 200:
                    print("✅ Server started successfully")
                    return process
        except Exception:
            pass

        time.sleep(0.5)
        if i % 4 == 3:
            print(f"  Waiting for server... ({i + 1}/{max_retries})")

    process.terminate()
    raise Exception("Server failed to start after 10 seconds")


async def main():
    """Run all endpoint tests."""
    print("\n")
    print("╔" + "═" * 58 + "╗")
    print("║" + " " * 10 + "END-TO-END API ENDPOINT TEST SUITE" + " " * 14 + "║")
    print("╚" + "═" * 58 + "╝")

    server_process = None

    try:
        # Start server
        server_process = await start_server()

        # Give server a moment to stabilize
        await asyncio.sleep(1)

        # Run tests
        results = {
            "Health Check": await test_health_endpoint(),
            "Valid PDF Upload": await test_ingest_upload_valid_pdf(),
            "Invalid File Type": await test_ingest_upload_invalid_file(),
            "Missing File": await test_ingest_upload_missing_file(),
        }

        # Summary
        print("\n" + "=" * 60)
        print("TEST SUMMARY")
        print("=" * 60)

        for test_name, passed in results.items():
            status = "✅ PASS" if passed else "❌ FAIL"
            print(f"{test_name:.<40} {status}")

        all_passed = all(results.values())
        if all_passed:
            print("\n🎉 All tests passed! API is working correctly.")
        else:
            failed_count = sum(1 for v in results.values() if not v)
            print(f"\n⚠️  {failed_count} test(s) failed. Check logs above.")

        return all_passed

    except Exception as e:
        print(f"\n❌ Test suite failed: {str(e)}")
        return False

    finally:
        # Cleanup
        if server_process:
            print("\nShutting down server...")
            server_process.terminate()
            try:
                server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_process.kill()
            print("✅ Server stopped")


if __name__ == "__main__":
    # Check env
    if not os.getenv("GEMINI_API_KEY"):
        print("\n⚠️  WARNING: GEMINI_API_KEY not set!")
        print("   Tests will run but agent processing may fail.")

    # Run tests
    success = asyncio.run(main())
    sys.exit(0 if success else 1)

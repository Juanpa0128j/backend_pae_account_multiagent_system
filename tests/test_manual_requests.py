#!/usr/bin/env python
"""
Manual API tester - Make requests to a running server.
Run this when you already have the server running in another terminal.

Usage:
    # Terminal 1:
    uv run python main.py

    # Terminal 2:
    uv run python test_manual_requests.py
"""

import json
import asyncio
from pathlib import Path
from reportlab.pdfgen import canvas
import httpx

BASE_URL = "http://localhost:8000"


def create_test_pdf(filename: str = "test_manual.pdf") -> str:
    """Create a simple test receipt PDF."""
    pdf_path = Path(filename)
    c = canvas.Canvas(str(pdf_path))

    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, 750, "RECIBO DE PAGO")

    c.setFont("Helvetica", 11)
    y = 700
    lines = [
        "Fecha: 18 de febrero de 2026",
        "Banco: Bancolombia S.A.",
        "Concepto: Servicios y transacciones",
        "Beneficiario: Empresa XYZ SAS",
        "Monto: $250.500",
        "Referencia: REF-2026-005678",
    ]

    for line in lines:
        c.drawString(50, y, line)
        y -= 30

    c.showPage()
    c.save()
    return str(pdf_path)


async def request_health():
    """GET /health"""
    print("\n" + "─" * 60)
    print("REQUEST: GET /health")
    print("─" * 60)

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{BASE_URL}/health")

        print(f"Status: {response.status_code}")
        print(f"Body: {json.dumps(response.json(), indent=2)}")

        return response.status_code == 200


async def request_ingest_upload():
    """POST /api/v1/ingest/upload"""
    print("\n" + "─" * 60)
    print("REQUEST: POST /api/v1/ingest/upload")
    print("─" * 60)

    pdf_path = create_test_pdf()
    print(f"Created test PDF: {pdf_path}")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            with open(pdf_path, "rb") as f:
                files = {"file": ("recibo_test.pdf", f, "application/pdf")}

                print(f"Uploading: {pdf_path}")
                response = await client.post(
                    f"{BASE_URL}/api/v1/ingest/upload", files=files
                )

        print(f"Status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            print("Body:")
            print(f"  - message: {data.get('message')}")
            print(f"  - ingest_id: {data.get('ingest_id')}")
            print(f"  - status: {data.get('status')}")
            return True
        else:
            print(f"Error: {response.text}")
            return False

    finally:
        Path(pdf_path).unlink(missing_ok=True)


async def request_ingest_invalid():
    """POST /api/v1/ingest/upload with invalid file"""
    print("\n" + "─" * 60)
    print("REQUEST: POST /api/v1/ingest/upload (invalid file)")
    print("─" * 60)

    # Create a text file
    txt_file = "test_invalid.txt"
    with open(txt_file, "w") as f:
        f.write("This is not a PDF")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            with open(txt_file, "rb") as f:
                files = {"file": ("invalid.txt", f, "text/plain")}
                response = await client.post(
                    f"{BASE_URL}/api/v1/ingest/upload", files=files
                )

        print(f"Status: {response.status_code}")
        print(f"Body: {response.json()}")

        if response.status_code == 400:
            print("✅ Correctly rejected (as expected)")
            return True
        else:
            return False

    finally:
        Path(txt_file).unlink(missing_ok=True)


async def main():
    print("\n╔" + "─" * 58 + "╗")
    print("║" + " " * 15 + "MANUAL API REQUESTS" + " " * 24 + "║")
    print("║" + " " * 10 + "Testing endpoints against running server" + " " * 7 + "║")
    print("╚" + "─" * 58 + "╝")

    print(f"\nTarget: {BASE_URL}")
    print("Make sure the server is running!")

    try:
        # Test 1: Health
        print("\n\n[1/3] Testing health endpoint...")
        health_ok = await request_health()

        # Test 2: Valid PDF
        print("\n\n[2/3] Testing PDF upload (valid)...")
        upload_ok = await request_ingest_upload()

        # Test 3: Invalid file
        print("\n\n[3/3] Testing PDF upload (invalid)...")
        invalid_ok = await request_ingest_invalid()

        # Summary
        print("\n\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"Health Check: {'✅ PASS' if health_ok else '❌ FAIL'}")
        print(f"Valid PDF Upload: {'✅ PASS' if upload_ok else '❌ FAIL'}")
        print(f"Invalid File Rejection: {'✅ PASS' if invalid_ok else '❌ FAIL'}")

        if health_ok and upload_ok and invalid_ok:
            print("\n🎉 All requests successful!")
        else:
            print("\n⚠️  Some requests failed")

    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        print("\nMake sure:")
        print("  1. Server is running: uv run python main.py")
        print("  2. GEMINI_API_KEY is set in .env")
        print("  3. You're in the correct directory")


if __name__ == "__main__":
    asyncio.run(main())

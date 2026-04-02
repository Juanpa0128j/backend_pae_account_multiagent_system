#!/usr/bin/env python
"""
Quick test script for the pilot agent.
Run this to validate the agent is working correctly.

Usage:
    python test_agent_quick.py
"""

import os
import sys
from pathlib import Path
import pytest
from reportlab.pdfgen import canvas

pytestmark = pytest.mark.skip(
    reason="Manual smoke script; not an automated pytest module"
)

# Load environment variables from .env
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from app.agents.graph import invoke_ingest_pipeline  # noqa: E402
from app.core.gemini_client import GeminiClient  # noqa: E402


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


def test_gemini_connection():
    """Test Gemini API connection."""
    print("\n" + "=" * 60)
    print("TEST 1: Gemini Connection")
    print("=" * 60)

    try:
        client = GeminiClient()
        print("✅ Gemini client initialized successfully")

        # Test simple extraction
        test_text = "Fecha: 2026-02-18\nMonto: 50000\nConcepto: Pago"
        result = client.extract_receipt_data(test_text)

        if isinstance(result, dict) and "fecha" in result:
            print("✅ Gemini extraction works!")
            print(f"   Sample result: {result}")
        else:
            print("❌ Gemini returned unexpected format")
            return False

        return True

    except Exception as e:
        print(f"❌ Gemini test failed: {str(e)}")
        print("   Make sure GEMINI_API_KEY is set in .env")
        return False


def test_pdf_processing():
    """Test PDF creation and processing."""
    print("\n" + "=" * 60)
    print("TEST 2: PDF Creation & Text Extraction")
    print("=" * 60)

    try:
        # Create test PDF
        pdf_path = create_test_pdf()
        print(f"✅ Test PDF created: {pdf_path}")

        # Extract text
        from app.services.pdf_processor import extract_text_from_pdf

        text = extract_text_from_pdf(pdf_path)

        if text and len(text) > 50:
            print(f"✅ Text extracted successfully ({len(text)} chars)")
            print(f"   Preview: {text[:100]}...")
        else:
            print("❌ PDF extraction failed or text too short")
            return False

        return pdf_path

    except Exception as e:
        print(f"❌ PDF test failed: {str(e)}")
        return None


def test_full_agent(pdf_path: str):
    """Test the complete agent pipeline."""
    print("\n" + "=" * 60)
    print("TEST 3: Full Agent Pipeline")
    print("=" * 60)

    try:
        print(f"Processing: {pdf_path}")
        result = invoke_ingest_pipeline(pdf_path)

        if result.get("status") == "completed":
            print("✅ Agent completed successfully!")
            print(f"   Process ID: {result.get('process_id')}")
            print(f"   Message: {result.get('message')}")

            data = result.get("data", {})
            print("\n   Extracted Data:")
            for key, value in data.items():
                print(f"   - {key}: {value}")

            return True
        else:
            print(f"❌ Agent failed: {result.get('error')}")
            return False

    except Exception as e:
        print(f"❌ Agent test failed: {str(e)}")
        import traceback

        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    print("\n")
    print("╔" + "═" * 58 + "╗")
    print("║" + " " * 15 + "PILOT AGENT TEST SUITE" + " " * 21 + "║")
    print("╚" + "═" * 58 + "╝")

    # Check env
    if not os.getenv("GEMINI_API_KEY"):
        print("\n⚠️  WARNING: GEMINI_API_KEY not set!")
        print("   Set it before running tests:")
        print("   - Windows: $env:GEMINI_API_KEY='your-key'")
        print("   - Linux: export GEMINI_API_KEY='your-key'")
        print("   - Or add to .env file\n")

    # Test 1: Gemini
    gemini_ok = test_gemini_connection()
    if not gemini_ok:
        print("\n❌ Cannot proceed without Gemini. Exiting.")
        return

    # Test 2: PDF
    pdf_path = test_pdf_processing()
    if not pdf_path:
        print("\n❌ Cannot proceed without PDF. Exiting.")
        return

    # Test 3: Full agent
    agent_ok = test_full_agent(pdf_path)

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    print(f"Gemini Connection: {'✅ PASS' if gemini_ok else '❌ FAIL'}")
    print(f"PDF Processing:    {'✅ PASS' if pdf_path else '❌ FAIL'}")
    print(f"Agent Pipeline:    {'✅ PASS' if agent_ok else '❌ FAIL'}")

    if gemini_ok and pdf_path and agent_ok:
        print("\n🎉 All tests passed! Agent is ready to use.")
    else:
        print("\n⚠️  Some tests failed. Check logs above.")

    # Cleanup
    if pdf_path and Path(pdf_path).exists():
        Path(pdf_path).unlink()
        print("\n🗑️  Cleaned up test PDF")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Integration test script for the ingestion pipeline.

Tests real documents through LlamaParse → classification → extraction → schema validation.
Results are saved to scripts/ingest_test_results.json for review.

Usage:
    python scripts/test_ingest_pipeline.py --all
    python scripts/test_ingest_pipeline.py --file "ejemplos_docs_ingesta/CONTABILIDAD ENERO/CONTABILIDAD ENERO/FV 192.jpg"
    python scripts/test_ingest_pipeline.py --dir ejemplos_docs_ingesta --no-cache

Requirements:
    GEMINI_API_KEY and LLAMA_CLOUD_API_KEY must be set in environment or .env file.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

SUPPORTED_EXTENSIONS = {".pdf", ".xlsx", ".jpg", ".jpeg", ".png"}
CACHE_DIR_NAME = ".parse_cache"

# ─── Colour helpers ──────────────────────────────────────────────────────────

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"


def ok(msg): print(f"{GREEN}✓ {msg}{RESET}")
def fail(msg): print(f"{RED}✗ {msg}{RESET}")
def warn(msg): print(f"{YELLOW}⚠ {msg}{RESET}")
def info(msg): print(f"{CYAN}  {msg}{RESET}")


# ─── LlamaParse extraction with caching ──────────────────────────────────────

def get_cache_path(file_path: Path, cache_dir: Path) -> Path:
    """Return the cache file path for a given document."""
    safe_name = file_path.name.replace(" ", "_") + ".md"
    return cache_dir / safe_name


def extract_text_llamaparse(file_path: Path, cache_dir: Path, no_cache: bool = False) -> str:
    """
    Parse a document with LlamaParse (supports PDF, JPG, PNG).
    Caches the markdown output to avoid repeated API calls.
    """
    cache_path = get_cache_path(file_path, cache_dir)

    if not no_cache and cache_path.exists():
        info(f"  [cache] Using cached parse for {file_path.name}")
        return cache_path.read_text(encoding="utf-8")

    api_key = os.environ.get("LLAMA_CLOUD_API_KEY")
    if not api_key:
        raise ValueError("LLAMA_CLOUD_API_KEY not set")

    try:
        from llama_parse import LlamaParse
    except ImportError:
        raise RuntimeError("llama-parse not installed. Run: uv add llama-parse")

    info(f"  [llamaparse] Parsing {file_path.name}...")
    is_image = file_path.suffix.lower() in (".jpg", ".jpeg", ".png")
    # fast_mode is PDF-only; images use default mode (already fast - single page OCR)
    parser = LlamaParse(api_key=api_key, result_type="markdown", fast_mode=not is_image)
    documents = parser.load_data(str(file_path))
    text = "\n\n".join(doc.text for doc in documents)

    # Save to cache
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(text, encoding="utf-8")
    info(f"  [cache] Saved to {cache_path.name}")

    return text


def extract_text_excel(file_path: Path) -> str:
    """Parse an Excel file and return markdown text."""
    from app.services.excel_parser import parse_excel
    raw_text, _ = parse_excel(str(file_path))
    return raw_text


# ─── Pipeline steps ──────────────────────────────────────────────────────────

def classify(text: str, source_format: str) -> dict:
    from app.services.doc_classifier import classify_document
    result = classify_document(text[:3000], source_format)
    return {
        "doc_type": result.doc_type.value,
        "pathway": result.pathway.value,
        "confidence": result.confidence,
        "period_start": result.period_start,
        "period_end": result.period_end,
        "entity_nit": result.entity_nit,
        "entity_name": result.entity_name,
    }


def extract(text: str, doc_type: str, gemini_client) -> dict:
    from app.agents.ingest_agent import _EXTRACT_METHOD_MAP
    method_name = _EXTRACT_METHOD_MAP.get(doc_type, "extract_transactions")
    method = getattr(gemini_client, method_name, None)
    if method is None:
        raise ValueError(f"GeminiClient has no method: {method_name}")
    return method(text)


def validate_schema(data: dict, doc_type: str) -> dict:
    """Validate extracted data against the Pydantic schema for this doc type."""
    from app.models.ingest_schemas import INGEST_CONTENT_SCHEMAS
    from pydantic import ValidationError

    schema_cls = INGEST_CONTENT_SCHEMAS.get(doc_type)
    if schema_cls is None:
        return {"valid": None, "error": f"No schema registered for doc_type '{doc_type}'"}

    try:
        schema_cls.model_validate(data)
        return {"valid": True, "error": None}
    except ValidationError as e:
        return {"valid": False, "error": str(e)[:500]}


def get_key_fields(data: dict, doc_type: str) -> dict:
    """Extract a few key fields for the summary report."""
    key: dict = {}
    if doc_type in ("factura_venta", "factura_compra"):
        key["consecutivo"] = data.get("consecutivo")
        emisor = data.get("emisor") or data.get("proveedor") or {}
        key["emisor_nit"] = emisor.get("nit") if isinstance(emisor, dict) else None
        totales = data.get("totales") or {}
        key["total_a_pagar"] = totales.get("total_a_pagar") if isinstance(totales, dict) else None
    elif doc_type == "extracto_bancario":
        key["entidad"] = data.get("entidad_financiera")
        key["cuenta"] = data.get("numero_cuenta")
        key["saldo_inicial"] = data.get("saldo_inicial")
        key["saldo_final"] = data.get("saldo_final")
        key["n_movimientos"] = len(data.get("movements") or [])
    elif doc_type in ("balance_general", "estado_resultados"):
        key["tipo"] = data.get("tipo")
        key["periodo_fin"] = data.get("periodo_fin")
        key["total_activos"] = data.get("total_activos")
        key["utilidad_neta"] = data.get("utilidad_neta")
        key["n_cuentas"] = len(data.get("accounts") or [])
    elif doc_type in ("declaracion_iva", "declaracion_reteica", "declaracion_ica"):
        key["nit_declarante"] = data.get("nit_declarante")
        key["periodo"] = data.get("periodo") or data.get("anio")
        key["total_a_pagar"] = data.get("total_a_pagar") or (data.get("liquidacion") or {}).get("total_a_pagar")
    elif doc_type == "autorretencion_ica":
        key["nit_declarante"] = data.get("nit_declarante")
        key["municipio"] = data.get("municipio")
        key["total_autorretenciones"] = data.get("total_autorretenciones")
    elif doc_type in ("auxiliar_impuesto", "auxiliar_iva", "libro_auxiliar"):
        key["cuenta_principal"] = data.get("cuenta_principal")
        key["periodo"] = data.get("periodo") or data.get("periodo_fin")
        key["n_lineas"] = len(data.get("lines") or data.get("cuentas") or [])
    elif doc_type in ("comprobante_egreso", "recibo_caja"):
        key["numero"] = data.get("numero_comprobante") or data.get("numero_recibo")
        key["fecha"] = data.get("fecha")
        key["valor"] = data.get("valor_bruto") or data.get("valor")
    elif doc_type == "nomina":
        key["empresa"] = (data.get("empresa") or {}).get("razon_social")
        key["periodo"] = data.get("periodo_fin")
        key["total_neto_pagar"] = data.get("total_neto_pagar")
        key["n_empleados"] = len(data.get("empleados") or [])
    elif doc_type == "conciliacion_bancaria":
        key["entidad"] = data.get("entidad_financiera")
        key["saldo_extracto"] = data.get("saldo_segun_extracto")
        key["saldo_libros"] = data.get("saldo_segun_libros")
    elif doc_type in ("planilla_seguridad_social",):
        key["empresa"] = (data.get("empresa") or {}).get("razon_social")
        key["total_a_pagar"] = data.get("total_a_pagar")
    elif doc_type == "recibo_pago_impuesto":
        key["tipo_impuesto"] = data.get("tipo_impuesto")
        key["total_pagado"] = data.get("total_pagado")
        key["periodo"] = data.get("periodo_gravable")
    else:
        # Generic: show first 5 non-null top-level fields
        for k, v in data.items():
            if v is not None and k != "informacion_adicional":
                key[k] = v
            if len(key) >= 5:
                break
    return key


# ─── Process a single file ───────────────────────────────────────────────────

def process_file(file_path: Path, cache_dir: Path, gemini_client, no_cache: bool = False, doc_type_override: str | None = None) -> dict:
    result = {
        "file": str(file_path.relative_to(PROJECT_ROOT)),
        "timestamp": datetime.now().isoformat(),
        "status": "error",
        "doc_type": None,
        "confidence": None,
        "schema_valid": None,
        "schema_error": None,
        "key_fields": {},
        "error": None,
        "duration_s": None,
    }

    ext = file_path.suffix.lower()
    t0 = time.time()

    try:
        # Step 1: Extract text
        if ext == ".xlsx":
            raw_text = extract_text_excel(file_path)
            source_format = "xlsx"
        elif ext in (".jpg", ".jpeg", ".png"):
            raw_text = extract_text_llamaparse(file_path, cache_dir, no_cache)
            source_format = ext.lstrip(".")
        elif ext == ".pdf":
            raw_text = extract_text_llamaparse(file_path, cache_dir, no_cache)
            source_format = "pdf"
        else:
            raise ValueError(f"Unsupported format: {ext}")

        if not raw_text or len(raw_text.strip()) < 50:
            raise ValueError(f"Extracted text too short ({len(raw_text)} chars)")

        info(f"  Text: {len(raw_text)} chars")

        # Step 2: Classify (or use override)
        if doc_type_override:
            classification = {"doc_type": doc_type_override, "confidence": 1.0}
            info(f"  Type: {doc_type_override} (overridden)")
        else:
            classification = classify(raw_text, source_format)
        result["doc_type"] = classification["doc_type"]
        result["confidence"] = classification["confidence"]
        result["entity_nit"] = classification.get("entity_nit")
        result["entity_name"] = classification.get("entity_name")
        if not doc_type_override:
            info(f"  Type: {classification['doc_type']} (confidence={classification['confidence']:.2f})")

        # Step 3: Extract
        extracted = extract(raw_text, classification["doc_type"], gemini_client)
        result["extracted_fields"] = list(extracted.keys())

        # Step 4: Validate schema
        validation = validate_schema(extracted, classification["doc_type"])
        result["schema_valid"] = validation["valid"]
        result["schema_error"] = validation["error"]

        # Step 5: Key fields summary
        result["key_fields"] = get_key_fields(extracted, classification["doc_type"])

        result["status"] = "ok"

    except Exception as e:
        result["error"] = str(e)
        result["status"] = "error"

    result["duration_s"] = round(time.time() - t0, 1)
    return result


# ─── Collect files ───────────────────────────────────────────────────────────

def collect_files(base_dir: Path) -> list[Path]:
    """Collect all supported files from a directory recursively."""
    files = []
    for ext in SUPPORTED_EXTENSIONS:
        files.extend(base_dir.rglob(f"*{ext}"))
        files.extend(base_dir.rglob(f"*{ext.upper()}"))
    # De-duplicate and sort
    return sorted(set(files))


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Test the ingestion pipeline against real documents.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Process all files in --dir")
    group.add_argument("--file", help="Process a single file")
    parser.add_argument(
        "--dir",
        default="ejemplos_docs_ingesta",
        help="Directory with documents to test (default: ejemplos_docs_ingesta)",
    )
    parser.add_argument("--no-cache", action="store_true", help="Force re-parse (ignore cache)")
    parser.add_argument(
        "--doc-type",
        help="Override doc_type for all files (skips classification — useful when quota is limited)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="Seconds to wait between files to avoid Gemini rate limits (default: 3)",
    )
    parser.add_argument(
        "--output",
        default="scripts/ingest_test_results.json",
        help="Output file for results (default: scripts/ingest_test_results.json)",
    )
    args = parser.parse_args()

    # Validate env
    if not os.environ.get("GEMINI_API_KEY"):
        print(f"{RED}Error: GEMINI_API_KEY not set{RESET}")
        sys.exit(1)

    # Initialise LLM client (OpenAI primary → Gemini → Groq)
    from app.core.llm_client import get_llm_client
    print(f"\n{BOLD}Initialising LLM client (OpenAI primary)...{RESET}")
    gemini_client = get_llm_client()
    print(f"{GREEN}OK{RESET}")

    # Collect files
    if args.file:
        files = [Path(args.file).resolve()]
        if not files[0].exists():
            print(f"{RED}File not found: {args.file}{RESET}")
            sys.exit(1)
    else:
        base_dir = PROJECT_ROOT / args.dir
        if not base_dir.exists():
            print(f"{RED}Directory not found: {base_dir}{RESET}")
            sys.exit(1)
        files = collect_files(base_dir)
        print(f"\n{BOLD}Found {len(files)} files in {base_dir}{RESET}")

    # Cache dir
    if args.file:
        cache_dir = Path(args.file).resolve().parent / CACHE_DIR_NAME
    else:
        cache_dir = PROJECT_ROOT / args.dir / CACHE_DIR_NAME

    # Process
    results = []
    passed = 0
    failed = 0
    schema_ok = 0
    schema_fail = 0

    for i, file_path in enumerate(files, 1):
        print(f"\n{BOLD}[{i}/{len(files)}] {file_path.name}{RESET}")
        result = process_file(
            file_path, cache_dir, gemini_client,
            no_cache=args.no_cache,
            doc_type_override=args.doc_type,
        )
        results.append(result)
        if i < len(files) and args.delay > 0:
            time.sleep(args.delay)

        if result["status"] == "ok":
            passed += 1
            ok(f"  Classified as: {result['doc_type']} ({result['confidence']:.0%} confidence)")
            if result["schema_valid"] is True:
                schema_ok += 1
                ok(f"  Schema validation: PASS")
            elif result["schema_valid"] is False:
                schema_fail += 1
                fail(f"  Schema validation: FAIL — {result['schema_error']}")
            else:
                warn(f"  Schema validation: SKIP (no schema for {result['doc_type']})")

            for k, v in result.get("key_fields", {}).items():
                if v is not None:
                    info(f"  {k}: {v}")
        else:
            failed += 1
            fail(f"  ERROR: {result['error']}")

        info(f"  Duration: {result['duration_s']}s")

    # Summary
    print(f"\n{BOLD}{'═'*60}{RESET}")
    print(f"{BOLD}SUMMARY{RESET}")
    print(f"  Total files:        {len(files)}")
    print(f"  {GREEN}Processed OK:       {passed}{RESET}")
    print(f"  {RED}Errors:             {failed}{RESET}")
    print(f"  {GREEN}Schema valid:       {schema_ok}{RESET}")
    print(f"  {RED}Schema invalid:     {schema_fail}{RESET}")

    # Save results
    output_path = PROJECT_ROOT / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "run_at": datetime.now().isoformat(),
                "total": len(files),
                "passed": passed,
                "failed": failed,
                "schema_ok": schema_ok,
                "schema_fail": schema_fail,
                "results": results,
            },
            f,
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    print(f"\n{CYAN}Results saved to: {output_path}{RESET}\n")


if __name__ == "__main__":
    main()

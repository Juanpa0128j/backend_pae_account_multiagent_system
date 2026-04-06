import json
import urllib.request

BASE = "http://127.0.0.1:8011"
PROCESS_IDS = [
    "proc_1774291966_ebcbacc2",
    "proc_1774292001_fa00f321",
    "proc_1774292021_95909407",
    "proc_1774292055_92c0ba94",
]


def get_json(url: str) -> dict:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw}


def guess_provider(text: str) -> str:
    t = (text or "").lower()
    if "openai" in t or "gpt-" in t:
        return "openai"
    if (
        "gemini" in t
        or "resource_exhausted" in t
        or "generativelanguage.googleapis.com" in t
    ):
        return "gemini"
    if "groq" in t:
        return "groq"
    return "unknown"


for pid in PROCESS_IDS:
    status = get_json(f"{BASE}/api/v1/process/status/{pid}")
    result = get_json(f"{BASE}/api/v1/process/result/{pid}")

    msg = (
        status.get("error_message")
        or result.get("error_message")
        or status.get("message")
        or result.get("message")
        or ""
    )
    provider = guess_provider(msg)

    print(f"\n{pid}")
    print(
        json.dumps(
            {
                "status": status.get("status"),
                "current_stage": status.get("current_stage"),
                "error_message": msg,
                "provider_guess": provider,
            },
            ensure_ascii=True,
        )
    )

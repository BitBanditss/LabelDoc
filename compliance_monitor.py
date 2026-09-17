"""
E-Commerce Compliance Monitor — decoupled, feature-flagged module.

- Independent from the existing package-label inspection flow.
- Owns its own MongoDB collection: `compliance_scan_results`.
- Background job runs as an asyncio task (non-blocking, no external broker needed).
  Drop-in replaceable with Celery/BullMQ: swap `_kickoff_job` for celery.send_task().
- Silently fails: LLM/scrape errors mark the DB row FAILED and log; main app unaffected.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from openai import AsyncOpenAI
from pydantic import BaseModel

log = logging.getLogger("compliance_monitor")

FEATURE_ENABLED = os.environ.get("ENABLE_COMPLIANCE_MONITOR", "1") == "1"


# ---- Models ----------------------------------------------------------------

class ScanRequest(BaseModel):
    url: str


class ComplianceScanResult(BaseModel):
    id: str
    url: str
    status: str          # QUEUED | RUNNING | COMPLETED | FAILED
    created_at: str
    updated_at: str
    ai_response: dict | None = None
    scraped_text_preview: str | None = None
    error: str | None = None


# ---- LLM prompt (PCR-2011 auditor) ----------------------------------------

SYSTEM_MESSAGE = (
    "You are a Legal Metrology Expert auditing e-commerce platforms for compliance "
    "with India's Packaged Commodities Rules (PCR) 2011."
)

USER_PROMPT_TEMPLATE = """I will provide you with the scraped text from an e-commerce product listing. \
Your job is to scan the text and verify if the mandatory declarations required under \
Rule 6(10) of PCR 2011 are visibly declared on the page.

The Mandatory Declarations to Check:
1. Manufacturer/Packer/Importer Details: Full name and address.
2. Country of Origin: Mandatory for both imported and domestic products.
3. Generic/Common Name: The actual name of the commodity.
4. Net Quantity: Expressed in standard units (weight, volume, length, or number).
5. Maximum Retail Price (MRP): Must be stated as inclusive of all taxes.
6. Consumer Care Details: Must include an address, phone number, and email address.
7. Month and Year of Manufacture: Mandatory for all products except those exempted by law.
8. Expiry Date: Mandatory for products with a defined shelf life.
(Note: Month and year of manufacture are explicitly exempted for e-commerce display under \
these rules and should not be penalized.)

Output Format: You must return ONLY a raw JSON object with the following structure:
{{
  "is_compliant": boolean,
  "missing_mandates": ["list of rules completely missing from the listing"],
  "partial_mandates": ["list of rules present but incomplete, e.g., missing email in consumer care"],
  "extracted_data": {{
    "mrp": "value or null",
    "country_of_origin": "value or null",
    "net_quantity": "value or null",
    "manufacturer_details": "value or null",
    "expiry_date": "value or null",
    "consumer_care": "value or null"
  }},
  "risk_level": "High/Medium/Low",
  "recommended_action": "Brief instructions on what the seller needs to fix"
}}

---
SCRAPED LISTING TEXT:
{scraped_text}
---

Return ONLY the JSON. No prose, no fences, no markdown."""


# ---- Scraper ---------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0 Safari/537.36"
)


def _extract_visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "svg"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text)[:12000]


def scrape_url(url: str, timeout: int = 12) -> tuple[str, str]:
    """Blocking HTTP fetch — returns (resolved_url, visible_text). Never raises."""
    resolved = url
    text = ""
    try:
        r = requests.head(url, headers={"User-Agent": USER_AGENT}, timeout=timeout, allow_redirects=True)
        if r.status_code < 400:
            resolved = r.url
    except Exception as e:
        log.warning("head redirect probe failed for %s: %s", url, e)

    try:
        r = requests.get(resolved, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en"},
                         timeout=timeout, allow_redirects=True)
        resolved = r.url
        if r.status_code < 400:
            text = _extract_visible_text(r.text)
    except Exception as e:
        log.warning("scrape failed for %s: %s", resolved, e)
    return resolved, text


# ---- LLM caller ------------------------------------------------------------

def _parse_json(raw: str) -> dict:
    raw = (raw or "").strip()
    raw = re.sub(r"^```(json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _llm_client() -> AsyncOpenAI:
    api_key  = os.environ.get("OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    base_url = os.environ.get("OPENAI_BASE_URL", None)
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return AsyncOpenAI(**kwargs)


async def audit_with_llm(text: str) -> dict:
    """Call the LLM with the PCR-2011 auditor prompt."""
    api_key = os.environ.get("OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY missing")

    model = os.environ.get("OPENAI_MODEL", "openai/gpt-oss-120b")
    oai = _llm_client()
    prompt = USER_PROMPT_TEMPLATE.format(scraped_text=text)

    collected = ""
    stream = await oai.chat.completions.create(
        model=model,
        stream=True,
        messages=[
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user",   "content": prompt},
        ],
    )
    async for chunk in stream:
        collected += chunk.choices[0].delta.content or ""

    parsed = _parse_json(collected)
    if not parsed:
        raise RuntimeError("LLM returned unparsable output")

    parsed.setdefault("is_compliant", False)
    parsed.setdefault("missing_mandates", [])
    parsed.setdefault("partial_mandates", [])
    parsed.setdefault("extracted_data", {})
    parsed.setdefault("risk_level", "Medium")
    parsed.setdefault("recommended_action", "")
    return parsed


# ---- Background job -------------------------------------------------------

async def _run_job(scan_id: str, url: str, db: AsyncIOMotorDatabase) -> None:
    col = db["compliance_scan_results"]
    try:
        await col.update_one({"id": scan_id}, {"$set": {"status": "RUNNING", "updated_at": _now()}})

        resolved_url, scraped = await asyncio.to_thread(scrape_url, url)
        await col.update_one({"id": scan_id}, {"$set": {
            "scraped_text_preview": (scraped or f"[scrape blocked — URL-only inference from {resolved_url}]")[:1200],
            "resolved_url": resolved_url,
        }})

        prompt_input = scraped or f"[Scraper was blocked. Infer best-effort from the URL alone: {resolved_url}]"
        ai = await audit_with_llm(prompt_input)

        await col.update_one({"id": scan_id}, {"$set": {
            "status": "COMPLETED",
            "ai_response": ai,
            "updated_at": _now(),
        }})
        log.info("compliance scan %s completed", scan_id)

    except Exception as e:
        log.exception("compliance scan %s failed", scan_id)
        try:
            await col.update_one({"id": scan_id}, {"$set": {
                "status": "FAILED",
                "error": str(e)[:400],
                "updated_at": _now(),
            }})
        except Exception:
            log.exception("failed to persist FAILED status for scan %s", scan_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- Router ---------------------------------------------------------------

def make_router(db_getter):
    """Build the router. `db_getter` is a zero-arg callable returning the Motor db."""
    router = APIRouter(prefix="/api/compliance-monitor", tags=["compliance-monitor"])

    @router.get("/status")
    async def status():
        return {"enabled": FEATURE_ENABLED, "version": "1.0"}

    @router.post("/scan", status_code=202)
    async def start_scan(req: ScanRequest):
        if not FEATURE_ENABLED:
            raise HTTPException(status_code=403, detail="Feature disabled")
        url = (req.url or "").strip()
        if not re.match(r"^https?://", url):
            raise HTTPException(status_code=400, detail="Invalid URL — must start with http(s)://")

        db = db_getter()
        scan_id = f"CS-{uuid.uuid4().hex[:12].upper()}"
        doc = {
            "id": scan_id, "url": url, "status": "QUEUED",
            "created_at": _now(), "updated_at": _now(),
            "ai_response": None, "scraped_text_preview": None, "error": None,
        }
        await db["compliance_scan_results"].insert_one(doc)
        asyncio.create_task(_run_job(scan_id, url, db))
        return {"id": scan_id, "status": "QUEUED"}

    @router.get("/scan/{scan_id}")
    async def get_scan(scan_id: str):
        if not FEATURE_ENABLED:
            raise HTTPException(status_code=403, detail="Feature disabled")
        db = db_getter()
        doc = await db["compliance_scan_results"].find_one({"id": scan_id}, {"_id": 0})
        if not doc:
            raise HTTPException(status_code=404, detail="Scan not found")
        return doc

    @router.get("/scans")
    async def list_scans(limit: int = 50):
        if not FEATURE_ENABLED:
            raise HTTPException(status_code=403, detail="Feature disabled")
        db = db_getter()
        docs = await db["compliance_scan_results"].find({}, {"_id": 0}).sort("created_at", -1).to_list(limit)
        return docs

    return router

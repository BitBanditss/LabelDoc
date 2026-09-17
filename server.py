from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import json
import logging
import re
from pathlib import Path
from pydantic import BaseModel
from typing import List, Optional, Any, Dict

from openai import AsyncOpenAI

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ.get('DB_NAME', 'labeldoc_database')]

OPENAI_API_KEY  = os.environ.get('OPENAI_API_KEY', '')
OPENAI_BASE_URL = os.environ.get('OPENAI_BASE_URL', 'https://api.groq.com/v1')
OPENAI_MODEL    = os.environ.get('OPENAI_MODEL', 'qwen/qwen3.6')

app = FastAPI(title="LabelDoc API")
api_router = APIRouter(prefix="/api")

from compliance_monitor import make_router as _cm_router  # noqa: E402
compliance_monitor_router = _cm_router(lambda: db)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("labeldoc")


# ---------------------------------------------------------------------------
# PCR 2011 — Complete Rule Reference
# ---------------------------------------------------------------------------
# Rule 6(1)(a)  : Name & address of manufacturer/packer/importer
# Rule 6(1)(aa) : Country of origin (mandatory for imported products; 2017 amendment)
# Rule 6(1)(b)  : Common/generic name of the commodity
# Rule 6(1)(c)  : Net quantity in standard SI units (weight/volume/number)
# Rule 6(1)(d)  : Month & year of manufacture/packing/import
#                 EXEMPTED for e-commerce (Rule 6(10))
#                 EXEMPTED for bidi, incense sticks, LPG cylinders
# Rule 6(1)(da) : Best-before / use-by date (if perishable) — 2017 amendment
# Rule 6(1)(e)  : MRP inclusive of all taxes, in ₹, rounded to nearest ₹ or 50p
#                 Format: "MRP Rs./₹ XX.XX (incl. of all taxes)" or equivalent
# Rule 6(1)(f)  : Dimensions/size (only where relevant, e.g. garments)
# Rule 6(2)     : Consumer care — name, address, telephone number, e-mail
# Rule 6(10)    : E-commerce must display all Rule 6(1) declarations EXCEPT mfg date
# Rule 7(3)     : Min font height 1 mm for all text; 2 mm if embossed/blown/molded
# Rule 8        : Width of letters ≥ 1/3 of height (except "1" and "I")
# Rule 9        : Declarations must be legible & prominent; MRP & net qty numerals
#                 must contrast conspicuously with background colour
# Rule 11       : Net quantity must EXCLUDE weight of packaging material
# ---------------------------------------------------------------------------

# MRP valid formats per Rule 6(1)(e) and official illustrations:
# (a) Maximum/Max. retail price Rs./₹ xx.xx (inclusive of all taxes)
# (b) Maximum/Max. retail price Rs./₹ xx.xx inclusive of all taxes
# (c) MRP Rs./₹ xx.xx incl. of all taxes
# (d) MRP Rs./₹ xx.xx (incl. of all taxes)

MRP_PATTERNS = [
    r'(maximum|max\.?)\s*retail\s*price',
    r'mrp',
]
MRP_CURRENCY = [r'₹', r'rs\.?', r'inr']
MRP_TAX_PHRASES = [
    r'inclusive\s+of\s+all\s+taxes',
    r'incl\.?\s*of\s*all\s*taxes',
    r'incl\.\s*all\s*taxes',
    r'including\s+all\s+taxes',
    r'incl\.\s*taxes',
]

# Net quantity valid SI units per Rule 6(1)(c) and Schedule II
NET_QTY_UNITS = r'(kg|kgs|kilogram|kilograms|g|gm|gms|gram|grams|mg|milligram|milligrams|l|ltr|ltrs|litre|litres|liter|liters|ml|millilitre|millilitres|milliliter|milliliters|cm|metre|metres|meter|meters|mm|nos|no|number|pcs|pieces|units)'

# Consumer care per Rule 6(2): must have address + telephone + email
PHONE_PATTERN = r'(\+91[-\s]?)?[6-9]\d{9}|\d{10,}|1[-\s]?800[-\s]?\d{3,}[-\s]?\d{4,}|1800[-\s]?\d{7,}'
EMAIL_PATTERN = r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
PINCODE_PATTERN = r'\b[1-9][0-9]{2}\s?[0-9]{3}\b'

# Mfg date per Rule 6(1)(d): Month and year (not just year)
MFG_DATE_PATTERNS = [
    r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s\.\-/]+20\d{2}\b',
    r'\b(01|02|03|04|05|06|07|08|09|10|11|12)[/\-\.]\d{4}\b',
    r'\b20\d{2}[\s\-/](01|02|03|04|05|06|07|08|09|10|11|12)\b',
]


def _llm_client() -> AsyncOpenAI:
    kwargs: Dict[str, Any] = {"api_key": OPENAI_API_KEY}
    if OPENAI_BASE_URL:
        kwargs["base_url"] = OPENAI_BASE_URL
    return AsyncOpenAI(**kwargs)


def _parse_llm_json(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    text = text.strip()
    text = re.sub(r'^```(json)?', '', text).strip()
    text = re.sub(r'```$', '', text).strip()

    # Try full parse first
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    try:
        return json.loads(text)
    except Exception:
        pass

    # Handle truncated JSON — close any open braces and retry
    try:
        truncated = text.strip()
        # Remove trailing incomplete key-value pair
        truncated = re.sub(r',\s*"[^"]*"\s*:\s*[^,}]*$', '', truncated)
        # Count open braces and close them
        open_braces = truncated.count('{') - truncated.count('}')
        truncated = truncated.rstrip(',').rstrip() + ('}' * max(0, open_braces))
        return json.loads(truncated)
    except Exception:
        pass

    # Last resort: extract key-value pairs with regex
    result = {}
    for m in re.finditer(r'"(\w+)"\s*:\s*("(?:[^"\\]|\\.)*"|true|false|null|[\d.]+)', text):
        key, val = m.group(1), m.group(2)
        try:
            result[key] = json.loads(val)
        except Exception:
            result[key] = val.strip('"'
        )
    return result


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class InspectionCreate(BaseModel):
    id: str
    inspectorId: str
    inspectorName: str
    role: str
    state: str
    zone: str
    district: str
    location: str
    scanType: str
    productName: str
    overall_status: str
    violation_count: int
    px_per_mm: Optional[float] = None
    barcode_calibrated: Optional[bool] = None
    timestamp: str
    extracted: Optional[Dict[str, Any]] = None
    results: List[Dict[str, Any]] = []
    imageUri: Optional[str] = None
    imageBase64: Optional[str] = None  # base64 proof photo
    url: Optional[str] = None

class ExtractRequest(BaseModel):
    image_base64: str
    mime_type: str = "image/jpeg"

class URLScanRequest(BaseModel):
    url: str


# ---------------------------------------------------------------------------
# AI Vision — OCR + NER
# ---------------------------------------------------------------------------
VISION_SYSTEM = (
    "You are LabelDoc, an expert OCR and NER engine for Indian Legal Metrology "
    "(Packaged Commodities) Rules 2011. "
    "IMPORTANT: Declarations on Indian packaged goods may appear on ANY side of the package — "
    "front, back, left, right, top, bottom, or on a seal/flap. "
    "Scan ALL visible text in the image thoroughly. "
    "If a declaration says it is printed elsewhere (e.g. 'see under the seal', "
    "'printed on pack', 'see top/bottom/side'), treat that as a valid declaration. "
    "Return ONLY a JSON object, no prose, no markdown fences."
)

VISION_PROMPT = (
    "Read ALL text on this Indian packaged commodity label (check all visible sides/panels). "
    "Return ONLY this JSON (null if not found anywhere on label):\n"
    "{\"productName\":\"\",\"genericName\":\"\",\"manufacturer\":\"\","
    "\"manufacturerAddress\":\"\",\"mrp\":\"\",\"netQuantity\":\"\","
    "\"manufacturingDate\":\"\",\"expiryDate\":\"\","
    "\"consumerCarePhone\":\"\",\"consumerCareEmail\":\"\","
    "\"consumerCareAddress\":\"\",\"countryOfOrigin\":\"\","
    "\"importerName\":\"\",\"barcode\":\"\",\"fssaiLicense\":\"\","
    "\"isImported\":false,\"isFood\":false,\"isMedicine\":false,\"isCosmetic\":false,"
    "\"font_heights_mm\":{\"mrp\":null,\"netQuantity\":null,\"manufacturer\":null,"
    "\"consumerCare\":null,\"genericName\":null},\"confidence\":0.9}\n"
    "IMPORTANT RULES: "
    "1. If MRP/MFD/expiry is on sealed edge or not visible, write 'See on pack' not null. "
    "2. If field is genuinely absent from entire product, write null. "
    "3. Toll-free numbers like 1-800-xxx-xxxx are valid consumer care phones. "
    "4. font_heights_mm = estimated mm height of capital letters in mm (MRP~2-4mm, address~1mm). "
    "5. isFood/isMedicine/isCosmetic = true if product belongs to that category."
)


async def call_vision_llm(image_base64: str, mime_type: str) -> Dict[str, Any]:
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured")

    logger.info("Vision LLM: model=%s", OPENAI_MODEL)

    # Use native google-genai SDK for Gemini models (most reliable for vision)
    # Falls back to OpenAI-compatible for other providers (Groq etc.)
    is_gemini = "gemini" in OPENAI_MODEL.lower() or "generativelanguage.googleapis.com" in (OPENAI_BASE_URL or "")

    collected = ""
    try:
        if is_gemini:
            import google.genai as genai
            import asyncio, base64, io
            gclient = genai.Client(api_key=OPENAI_API_KEY)
            # Strip data URI prefix if present (e.g. "data:image/jpeg;base64,...")
            b64_clean = image_base64
            if ',' in b64_clean:
                b64_clean = b64_clean.split(',', 1)[1]
            # Strip all whitespace — newlines and spaces corrupt base64
            b64_clean = b64_clean.strip().replace('\n','').replace('\r','').replace(' ','')
            # Fix padding
            b64_clean += '=' * (-len(b64_clean) % 4)
            logger.info("Decoding base64: length=%d", len(b64_clean))
            try:
                image_bytes = base64.b64decode(b64_clean, validate=True)
            except Exception as decode_err:
                logger.error("base64 decode failed: %s — trying without validate", decode_err)
                image_bytes = base64.b64decode(b64_clean + '==')
            logger.info("Decoded image bytes: %d", len(image_bytes))

            # Always convert to JPEG and resize — Gemini times out on webp/large images
            try:
                from PIL import Image as PILImage
                orig_size = len(image_bytes)
                img = PILImage.open(io.BytesIO(image_bytes)).convert("RGB")
                # Always resize to max 1024px on longest side
                # Resize aggressively — target under 50KB for fast Gemini response
                max_dim = 1024 if orig_size < 300_000 else 640
                if max(img.size) > max_dim:
                    ratio = max_dim / max(img.size)
                    img = img.resize(
                        (int(img.width * ratio), int(img.height * ratio)),
                        PILImage.LANCZOS
                    )
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=65, optimize=True)
                image_bytes = buf.getvalue()
                mime_type = "image/jpeg"
                logger.info("Image converted: %d -> %d bytes (%dx%d)", orig_size, len(image_bytes), img.width, img.height)
            except Exception as e:
                logger.warning("Image convert failed: %s", e)

            def _sync_call():
                resp = gclient.models.generate_content(
                    model=OPENAI_MODEL,
                    contents=[
                        genai.types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                        genai.types.Part.from_text(text=f"{VISION_SYSTEM}\n\n{VISION_PROMPT}"),
                    ],
                    config=genai.types.GenerateContentConfig(
                        max_output_tokens=2048,
                        temperature=0.1,
                        automatic_function_calling=genai.types.AutomaticFunctionCallingConfig(
                            disable=True,
                        ),
                    ),
                )
                # Extract text safely from response
                text = ""
                if hasattr(resp, "text") and resp.text:
                    text = resp.text
                elif hasattr(resp, "candidates") and resp.candidates:
                    for candidate in resp.candidates:
                        if hasattr(candidate, "content") and candidate.content:
                            for part in candidate.content.parts:
                                if hasattr(part, "text") and part.text:
                                    text += part.text
                logger.info("Gemini raw response length: %d chars", len(text))
                return text

            # Retry up to 3 times — handles 503 overload and cold-start timeouts
            last_error = None
            collected = ""
            for attempt in range(3):
                wait = [0, 5, 15][attempt]  # 0s, 5s, 15s backoff
                if wait:
                    logger.info("Retrying Gemini in %ds (attempt %d)...", wait, attempt+1)
                    await asyncio.sleep(wait)
                try:
                    collected = await asyncio.wait_for(
                        asyncio.to_thread(_sync_call),
                        timeout=120.0
                    )
                    if collected:
                        logger.info("Gemini response (attempt %d): %s", attempt+1, collected[:300])
                        break
                    else:
                        last_error = "Empty response from Gemini"
                        logger.warning("Empty response attempt %d", attempt+1)
                except asyncio.TimeoutError:
                    last_error = f"AI vision timed out (attempt {attempt+1})"
                    logger.warning("Gemini timeout attempt %d", attempt+1)
                except Exception as e:
                    last_error = str(e)
                    logger.warning("Gemini error attempt %d: %s", attempt+1, e)
            if not collected:
                raise HTTPException(status_code=502, detail=f"AI vision failed after 3 attempts: {last_error}")
        else:
            # OpenAI / Groq path
            oai = _llm_client()
            stream = await oai.chat.completions.create(
                model=OPENAI_MODEL,
                stream=True,
                max_tokens=900,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}},
                        {"type": "text", "text": f"{VISION_SYSTEM}\n\n{VISION_PROMPT}"},
                    ],
                }],
            )
            async for chunk in stream:
                collected += chunk.choices[0].delta.content or ""

    except Exception as e:
        logger.error("LLM vision failed: %s", e)
        raise HTTPException(status_code=502, detail=f"AI vision failed: {e}")

    parsed = _parse_llm_json(collected)
    logger.info("Vision extracted fields: %s", list(parsed.keys()))
    return parsed


# ---------------------------------------------------------------------------
# PCR 2011 Compliance Engine — Rule-accurate checks
# ---------------------------------------------------------------------------

def _check_mrp(val: str) -> List[str]:
    """Rule 6(1)(e) — MRP must include currency and 'inclusive of all taxes'.
    'See under the seal' is legally valid — MRP printed on sealed edge."""
    if _is_see_on_pack(val):
        return []   # Valid — printed on seal/pack edge
    issues = []
    low = val.lower()
    has_mrp_kw   = any(re.search(p, low) for p in MRP_PATTERNS)
    has_currency = any(re.search(p, low) for p in MRP_CURRENCY) or '₹' in val
    has_tax      = any(re.search(p, low) for p in MRP_TAX_PHRASES)
    has_amount   = bool(re.search(r'\d+(\.\d+)?', val))

    if not has_mrp_kw:
        issues.append("MRP prefix missing — must say 'MRP' or 'Maximum Retail Price' [Rule 6(1)(e)]")
    if not has_currency:
        issues.append("Currency symbol missing — must include '₹' or 'Rs.' [Rule 6(1)(e)]")
    if not has_amount:
        issues.append("MRP amount (numeric value) not found [Rule 6(1)(e)]")
    if not has_tax:
        issues.append("'Inclusive of all taxes' phrase missing — mandatory per Rule 6(1)(e)")
    return issues


def _check_net_qty(val: str) -> List[str]:
    """Rule 6(1)(c) — Net quantity in standard SI units."""
    issues = []
    has_number = bool(re.search(r'\d+(\.\d+)?', val))
    has_unit   = bool(re.search(NET_QTY_UNITS, val, re.IGNORECASE))
    if not has_number:
        issues.append("Net quantity must include a numeric value [Rule 6(1)(c)]")
    if not has_unit:
        issues.append(
            "Net quantity must be in standard SI units: "
            "kg/g/mg (weight), L/ml (volume), m/cm/mm (length), or Nos/Pcs (count) [Rule 6(1)(c)]"
        )
    return issues


# Phrases legally accepted as valid mfg date declarations on physical labels
_SEE_ON_PACK_PATTERNS = [
    r'see\s+(under|on)\s+the\s+(seal|pack|pouch|flap|bottom|lid|cap|wrapper)',
    r'printed\s+on\s+(the\s+)?(pack|pouch|seal|flap|bottom|lid|wrapper)',
    r'refer\s+(to\s+)?(the\s+)?(pack|pouch|seal|label)',
    r'on\s+the\s+(pack|pouch|seal|flap|bottom)',
    r'see\s+(pack|pouch|seal)',
]

def _is_see_on_pack(val: str) -> bool:
    """Returns True if value is a valid 'printed elsewhere on pack' declaration."""
    low = val.lower().strip()
    return any(re.search(p, low) for p in _SEE_ON_PACK_PATTERNS)

def _check_mfg_date(val: str) -> List[str]:
    """Rule 6(1)(d) — Month AND year required (not just year).
    'See under the seal' is legally valid — MFD printed on sealed edge."""
    if not val:
        return ["Month and year of manufacture/packing not declared [Rule 6(1)(d)]"]
    if _is_see_on_pack(val):
        return []   # Valid — printed on seal/pack edge
    low = val.lower()
    has_month_year = any(re.search(p, low) for p in MFG_DATE_PATTERNS)
    if not has_month_year:
        return [
            "Manufacturing date must include both MONTH and YEAR "
            "(e.g. '03/2024' or 'March 2024') — year alone is insufficient [Rule 6(1)(d)]"
        ]
    return []


def _check_consumer_care(phone: str, email: str, address: str) -> List[str]:
    """Rule 6(2) — Must have name/address + telephone + email."""
    issues = []
    combined = f"{phone or ''} {email or ''} {address or ''}".strip()
    if not combined:
        return ["Consumer care details completely missing — name, address, phone and email required [Rule 6(2)]"]

    has_phone  = bool(phone and re.search(PHONE_PATTERN, phone))
    has_email  = bool(email and re.search(EMAIL_PATTERN, email))
    has_addr   = bool(address and len(address.strip()) > 5)

    if not has_phone:
        issues.append("Consumer care telephone number missing or invalid [Rule 6(2)]")
    if not has_email:
        issues.append("Consumer care e-mail address missing [Rule 6(2)]")
    if not has_addr:
        issues.append("Consumer care address missing [Rule 6(2)]")
    return issues


def _check_manufacturer(name: str, address: str) -> List[str]:
    """Rule 6(1)(a) — Full name AND complete address required."""
    issues = []
    if not name or len(name.strip()) < 3:
        issues.append("Manufacturer/packer/importer name missing [Rule 6(1)(a)]")
    if not address or len(address.strip()) < 10:
        issues.append("Manufacturer complete address missing [Rule 6(1)(a)]")
    else:
        has_pincode = bool(re.search(PINCODE_PATTERN, address))
        if not has_pincode:
            issues.append("Manufacturer address should include 6-digit PIN code [Rule 6(1)(a)]")
    return issues


def _check_country_of_origin(val: str, is_imported: bool) -> List[str]:
    """Rule 6(1)(aa) — Mandatory for imported products; recommended for domestic."""
    if is_imported and (not val or len(val.strip()) < 2):
        return ["Country of origin mandatory for imported products [Rule 6(1)(aa)]"]
    return []


def _check_expiry_date(val: str, is_food: bool, is_medicine: bool, is_cosmetic: bool) -> List[str]:
    """
    Rule 6(1)(da) — Best before / expiry date.
    MANDATORY for: food products, medicines, cosmetics, baby food, pesticides.
    NOT mandatory for: non-food household items, garments, hardware, etc.
    Under FSSAI regulations, best-before is mandatory for ALL packaged food.
    """
    needs_expiry = is_food or is_medicine or is_cosmetic
    if not needs_expiry:
        return []  # Non-food/non-medicine products — not required

    if not val or len(val.strip()) < 2:
        category = "food products" if is_food else ("medicines" if is_medicine else "cosmetics")
        return [
            f"Best before / expiry date missing — mandatory for {category} "
            f"[Rule 6(1)(da) & FSSAI Regulations]"
        ]
    if _is_see_on_pack(val):
        return []  # Valid — printed on seal/pack

    # Check it looks like a date
    has_date = bool(re.search(
        r'(\d{1,2}[/\-\.]\d{2,4}|\d{4}|'
        r'jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)',
        val.lower()
    ))
    if not has_date:
        return ["Best before / expiry date format not recognised — must include month and year [Rule 6(1)(da)]"]
    return []


def _check_generic_name(val: str) -> List[str]:
    """Rule 6(1)(b) — Common/generic name of commodity."""
    if not val or len(val.strip()) < 2:
        return ["Common/generic name of commodity missing [Rule 6(1)(b)]"]
    return []


def evaluate_compliance(extracted: Dict[str, Any], scan_type: str = "IMAGE") -> Dict[str, Any]:
    """
    Full PCR 2011 compliance evaluation.
    scan_type: "IMAGE" = physical label, "URL" = e-commerce listing.
    For URL scans, manufacturing date is EXEMPTED per Rule 6(10).
    """
    is_ecommerce = scan_type.upper() == "URL"
    is_imported  = bool(extracted.get("isImported", False))
    is_food      = bool(extracted.get("isFood", False))
    is_medicine  = bool(extracted.get("isMedicine", False))
    is_cosmetic  = bool(extracted.get("isCosmetic", False))

    results = []

    def _add(field: str, label: str, clause: str, val: Any, issues: List[str]):
        status = "COMPLIANT" if not issues else "NON_COMPLIANT"
        results.append({
            "field":       field,
            "label":       label,
            "value":       val if val else "— (not found)",
            "rule_clause": clause,
            "status":      status,
            "violations":  issues,
        })

    # --- Rule 6(1)(b): Generic/common name ---
    generic = extracted.get("genericName") or extracted.get("productName")
    _add("genericName", "Common/Generic Name of Commodity", "Rule 6(1)(b)",
         generic, _check_generic_name(generic or ""))

    # --- Rule 6(1)(a): Manufacturer name & address ---
    mfr_name = extracted.get("manufacturer", "")
    mfr_addr = extracted.get("manufacturerAddress", "")
    mfr_issues = _check_manufacturer(mfr_name or "", mfr_addr or "")
    _add("manufacturer", "Manufacturer / Packer / Importer Name", "Rule 6(1)(a)",
         mfr_name, [i for i in mfr_issues if "address" not in i.lower()])
    _add("manufacturerAddress", "Manufacturer Complete Address", "Rule 6(1)(a)",
         mfr_addr, [i for i in mfr_issues if "address" in i.lower() or "pin" in i.lower()])

    # --- Rule 6(1)(c): Net quantity ---
    net_qty = extracted.get("netQuantity", "")
    _add("netQuantity", "Net Quantity (in SI units)", "Rule 6(1)(c)",
         net_qty,
         _check_net_qty(net_qty or "") if net_qty else
         ["Net quantity not declared — mandatory in standard SI units [Rule 6(1)(c)]"])

    # --- Rule 6(1)(d): Manufacturing date (exempted for e-commerce) ---
    mfg_date = extracted.get("manufacturingDate", "")
    if is_ecommerce:
        _add("manufacturingDate", "Month & Year of Manufacture",
             "Rule 6(1)(d) [EXEMPTED for e-commerce per Rule 6(10)]",
             mfg_date or "N/A (exempted for e-commerce)", [])
    else:
        _add("manufacturingDate", "Month & Year of Manufacture", "Rule 6(1)(d)",
             mfg_date,
             _check_mfg_date(mfg_date or "") if mfg_date else
             ["Month and year of manufacture/packing not declared [Rule 6(1)(d)]"])

    # --- Rule 6(1)(e): MRP ---
    mrp = extracted.get("mrp", "")
    _add("mrp", "Maximum Retail Price (MRP)", "Rule 6(1)(e)",
         mrp,
         _check_mrp(mrp or "") if mrp else
         ["MRP not declared — must show price inclusive of all taxes [Rule 6(1)(e)]"])

    # --- Rule 6(2): Consumer care ---
    cc_phone = extracted.get("consumerCarePhone", "")
    cc_email = extracted.get("consumerCareEmail", "")
    cc_addr  = extracted.get("consumerCareAddress", "") or mfr_addr
    cc_issues = _check_consumer_care(cc_phone or "", cc_email or "", cc_addr or "")
    consumer_care_val = " | ".join(filter(None, [cc_phone, cc_email, cc_addr]))
    _add("consumerCare", "Consumer Care (Phone + Email + Address)", "Rule 6(2)",
         consumer_care_val or None, cc_issues)

    # --- Rule 6(1)(aa): Country of origin ---
    coo = extracted.get("countryOfOrigin", "")
    coo_issues = _check_country_of_origin(coo or "", is_imported)
    _add("countryOfOrigin",
         "Country of Origin" + (" (MANDATORY — imported product)" if is_imported else " (if imported)"),
         "Rule 6(1)(aa)",
         coo, coo_issues)

    # --- Rule 6(1)(da): Expiry / Best Before date ---
    expiry = extracted.get("expiryDate", "")
    expiry_issues = _check_expiry_date(expiry or "", is_food, is_medicine, is_cosmetic)
    if is_food or is_medicine or is_cosmetic:
        _add("expiryDate",
             "Best Before / Expiry Date"
             + (" (MANDATORY — food product)" if is_food else
                " (MANDATORY — medicine)" if is_medicine else
                " (MANDATORY — cosmetic)"),
             "Rule 6(1)(da)",
             expiry, expiry_issues)

    # --- Importer details (if imported) ---
    if is_imported:
        importer = extracted.get("importerName", "")
        _add("importerName", "Importer Name & Address (MANDATORY — imported product)",
             "Rule 6(1)(a)",
             importer,
             ["Importer name and address missing — mandatory for imported products [Rule 6(1)(a)]"]
             if not importer else [])

    # --- Rule 7: Font height checks (min 1mm per Rule 7(3)) ---
    MIN_FONT_MM = 1.0
    font_heights = extracted.get("font_heights_mm") or {}
    font_field_map = {
        "mrp":          "Maximum Retail Price (MRP)",
        "netQuantity":  "Net Quantity (in SI units)",
        "manufacturer": "Manufacturer / Packer / Importer Name",
        "consumerCare": "Consumer Care (Phone + Email + Address)",
        "genericName":  "Common/Generic Name of Commodity",
    }
    # Attach font_height_mm to each result row
    for r in results:
        field_key = r["field"]
        fh = font_heights.get(field_key)
        r["font_height_mm"] = round(fh, 2) if isinstance(fh, (int, float)) else None
        # Add Rule 7 violation if font is below minimum
        if isinstance(fh, (int, float)) and fh < MIN_FONT_MM:
            r["violations"].append(
                f"Font height {fh:.1f}mm is below minimum 1mm required by Rule 7(3)"
            )
            r["status"] = "NON_COMPLIANT"

    # Summary
    violations_total = sum(1 for r in results if r["status"] == "NON_COMPLIANT")
    all_violations   = [v for r in results for v in r["violations"]]
    overall = "COMPLIANT" if violations_total == 0 else "NON_COMPLIANT"

    return {
        "overall_status":  overall,
        "violation_count": violations_total,
        "all_violations":  all_violations,
        "px_per_mm":       4.5,
        "barcode_calibrated": bool(extracted.get("barcode")),
        "scan_type":       scan_type,
        "is_ecommerce":    is_ecommerce,
        "results":         results,
        "pcr_note": (
            "Manufacturing date exempted for e-commerce per Rule 6(10)"
            if is_ecommerce else
            "Physical label audit per PCR 2011 Rules 6, 7, 9"
        ),
    }


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@api_router.get("/")
async def root():
    return {"message": "LabelDoc API online", "version": "2.0", "pcr": "2011"}


@api_router.post("/extract-image")
async def extract_image(req: ExtractRequest):
    if not req.image_base64:
        raise HTTPException(status_code=400, detail="image_base64 is required")

    b64 = req.image_base64
    logger.info("Received image_base64 length: %d, starts_with_data: %s, mime: %s",
                len(b64), b64.startswith("data:"), req.mime_type)
    if b64.startswith("data:"):
        # Extract mime type from data URI if present
        header, b64 = b64.split(",", 1)
        if ";" in header:
            req_mime = header.split(";")[0].replace("data:", "")
            if req_mime:
                mime_type_detected = req_mime
                logger.info("Detected mime from data URI: %s", mime_type_detected)
    # Strip any whitespace or newlines that corrupt base64
    b64 = b64.strip().replace("\n", "").replace("\r", "").replace(" ", "")
    # Fix missing base64 padding
    b64 += '=' * (-len(b64) % 4)
    logger.info("Clean b64 length: %d", len(b64))

    extracted = await call_vision_llm(b64, req.mime_type)
    compliance = evaluate_compliance(extracted, scan_type="IMAGE")
    return {"extracted": extracted, "compliance": compliance}


@api_router.post("/scan-url")
async def scan_url(req: URLScanRequest):
    url = (req.url or "").strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(status_code=400, detail="Invalid URL")

    from url_ai_extractor import ai_extract_from_url
    try:
        payload = await ai_extract_from_url(url)
    except Exception as e:
        logger.exception("scan-url failed")
        raise HTTPException(status_code=502, detail=f"AI extraction failed: {e}")

    extracted = {
        "genericName":         payload.get("genericName") or payload.get("productName"),
        "manufacturer":        payload.get("manufacturer"),
        "manufacturerAddress": payload.get("manufacturerAddress"),
        "mrp":                 payload.get("mrp"),
        "netQuantity":         payload.get("netQuantity"),
        "manufacturingDate":   None,   # exempted for e-commerce
        "consumerCarePhone":   payload.get("consumerCarePhone"),
        "consumerCareEmail":   payload.get("consumerCareEmail"),
        "consumerCareAddress": payload.get("consumerCareAddress"),
        "countryOfOrigin":     payload.get("countryOfOrigin"),
        "barcode":             None,
        "isImported":          bool(payload.get("importerName")),
        "confidence":          payload.get("confidence_overall", 0.7),
    }
    compliance = evaluate_compliance(extracted, scan_type="URL")
    return {
        "extracted":    extracted,
        "compliance":   compliance,
        "url":          url,
        "resolved_url": payload.get("resolved_url"),
        "ai_note":      payload.get("note"),
    }


@api_router.post("/ecommerce/extract-listing")
async def extract_listing(req: URLScanRequest):
    url = (req.url or "").strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(status_code=400, detail="Invalid URL")

    from url_ai_extractor import ai_extract_from_url
    try:
        payload = await ai_extract_from_url(url)
    except Exception as e:
        logger.exception("extract-listing failed")
        raise HTTPException(status_code=502, detail=f"AI extraction failed: {e}")

    return payload


@api_router.post("/inspections")
async def create_inspection(inspection: InspectionCreate):
    try:
        doc = inspection.dict()
        # Upsert by id — safe to call multiple times
        await db.inspections.update_one(
            {"id": inspection.id},
            {"$set": doc},
            upsert=True
        )
        return {"ok": True, "id": inspection.id}
    except Exception as e:
        logger.error("Failed to save inspection: %s", e)
        return {"ok": False, "id": inspection.id, "warning": "Saved locally only"}


@api_router.get("/inspections")
async def list_inspections(
    inspector_id: Optional[str] = None,
    state: Optional[str] = None,
    district: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 200
):
    try:
        query: Dict[str, Any] = {}
        if inspector_id:
            query["inspectorId"] = inspector_id
        if state:
            query["state"] = state
        if district:
            query["district"] = district
        if status:
            query["overall_status"] = status.upper()
        docs = await db.inspections.find(query, {"_id": 0}).sort("timestamp", -1).to_list(limit)
        return docs
    except Exception as e:
        logger.error("Failed to fetch inspections: %s", e)
        return []


@api_router.get("/inspections/{inspection_id}/docx")
async def download_inspection_docx(inspection_id: str):
    """Generate and return an editable DOCX compliance report."""
    from fastapi.responses import FileResponse
    from docx_report import generate_docx
    import asyncio
    try:
        doc = await db.inspections.find_one({"id": inspection_id}, {"_id": 0})
        if not doc:
            raise HTTPException(status_code=404, detail="Inspection not found")
        file_path = await asyncio.to_thread(generate_docx, doc)
        filename = f"LabelDoc_{inspection_id}.docx"
        return FileResponse(
            path=file_path,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=filename,
            background=None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("DOCX generation failed for %s", inspection_id)
        raise HTTPException(status_code=500, detail=f"DOCX generation failed: {e}")


@api_router.get("/inspections/{inspection_id}")
async def get_inspection(inspection_id: str):
    try:
        doc = await db.inspections.find_one({"id": inspection_id}, {"_id": 0})
        if not doc:
            raise HTTPException(status_code=404, detail="Inspection not found")
        return doc
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to fetch inspection %s: %s", inspection_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@api_router.delete("/inspections/{inspection_id}")
async def delete_inspection(inspection_id: str):
    try:
        result = await db.inspections.delete_one({"id": inspection_id})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Inspection not found")
        return {"ok": True, "id": inspection_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete inspection %s: %s", inspection_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/stats")
async def get_stats(inspector_id: Optional[str] = None, state: Optional[str] = None):
    """Aggregated compliance statistics from MongoDB."""
    try:
        query: Dict[str, Any] = {}
        if inspector_id:
            query["inspectorId"] = inspector_id
        if state:
            query["state"] = state
        pipeline = [
            {"$match": query},
            {"$group": {
                "_id": None,
                "total": {"$sum": 1},
                "compliant": {"$sum": {"$cond": [{"$eq": ["$overall_status", "COMPLIANT"]}, 1, 0]}},
                "non_compliant": {"$sum": {"$cond": [{"$eq": ["$overall_status", "NON_COMPLIANT"]}, 1, 0]}},
                "total_violations": {"$sum": "$violation_count"},
                "avg_violations": {"$avg": "$violation_count"},
            }},
        ]
        result = await db.inspections.aggregate(pipeline).to_list(1)
        if not result:
            return {"total": 0, "compliant": 0, "non_compliant": 0, "total_violations": 0, "avg_violations": 0}
        r = result[0]
        r.pop("_id", None)
        r["compliance_rate"] = round((r["compliant"] / r["total"]) * 100, 1) if r["total"] > 0 else 0
        r["avg_violations"] = round(r.get("avg_violations") or 0, 2)
        return r
    except Exception as e:
        logger.error("Stats failed: %s", e)
        return {"error": str(e)}


app.include_router(api_router)
app.include_router(compliance_monitor_router) 

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
    
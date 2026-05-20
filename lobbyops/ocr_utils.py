"""Parcel label extraction: Gemini Vision API with focused retry and pytesseract fallback."""

import os
import re
import base64
import json
import difflib
import tempfile
import logging
from typing import Dict

import cv2
import numpy as np
import pytesseract
import requests

log = logging.getLogger(__name__)

_UNIT_LISTS: dict = {}


def _load_unit_list(building: str) -> list:
    if building in _UNIT_LISTS:
        return _UNIT_LISTS[building]
    filename = f"units_{building.lower()}.txt"
    path = os.path.join(os.path.dirname(__file__), filename)
    if not os.path.exists(path):
        log.warning("unit list not found path=%s", path)
        _UNIT_LISTS[building] = []
        return []
    with open(path) as f:
        units = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    _UNIT_LISTS[building] = units
    log.info("loaded unit list building=%s count=%d", building, len(units))
    return units


def _sanitize(value: str) -> str:
    # Strip ASCII and Unicode control characters before returning any extracted field
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "", value).strip()


def _validate_unit(unit: str, building: str) -> str:
    units = _load_unit_list(building)
    if not units:
        return unit
    if unit in units:
        return unit
    matches = difflib.get_close_matches(unit, units, n=1, cutoff=0.85)
    if matches:
        corrected = matches[0]
        log.info("unit fuzzy corrected original=%s corrected=%s building=%s", unit, corrected, building)
        return corrected
    return unit


def preprocess_image(image_path: str) -> str:
    img = cv2.imread(image_path)
    if img is None:
        return image_path

    # Upscale small images so text is large enough for OCR
    h, w = img.shape[:2]
    if max(h, w) < 1200:
        scale = 1200 / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)

    img = cv2.fastNlMeansDenoisingColored(img, None, 10, 10, 7, 21)

    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    img = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)

    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    img = cv2.filter2D(img, -1, kernel)

    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    cv2.imwrite(tmp.name, img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return tmp.name


def guess_parcel_type(image_path: str) -> str:
    img = cv2.imread(image_path)
    if img is None:
        return "BROWN BOX"

    avg_color = cv2.mean(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))[:3]
    r, g, b = avg_color

    if max(r, g, b) < 60:
        color = "BLACK"
    elif r > 200 and g > 200 and b > 200:
        color = "WHITE"
    elif r > 200 and g > 180 and b < 130:
        color = "YELLOW"
    elif abs(r - g) < 15 and abs(g - b) < 15:
        color = "GREY"
    else:
        color = "BROWN"

    edges = cv2.Canny(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 100, 200)
    edge_density = np.sum(edges > 0) / edges.size
    pkg_type = "BOX" if edge_density > 0.08 else "PACKAGE"

    return f"{color} {pkg_type}".upper()


def _extract_unit_from_text(text: str) -> str:
    patterns = [
        r"(?:UNIT|APT|SUITE|APARTMENT|ROOM|RM|#)\s*[:#\-]?\s*(\d{1,5}[A-Z]?)\b",
        r"^(\d{3,5}[A-Z]?)\s*-\s*\d{1,3}\s+[A-Z]",
        r"^(\d{2,5}[A-Z]?)\s*[-,]",
        r"-\s*(\d{2,5}[A-Z]?)\s*$",
        r"^\s*(\d{2,4}[A-Z]?)\s*$",
        r"\b(\d{2,5}[A-Z]?)\b",
    ]
    for pat in patterns:
        for line in text.splitlines():
            m = re.search(pat, line.strip(), re.IGNORECASE)
            if m:
                return m.group(1).upper()
    return "UNKNOWN"


def _extract_name_from_text(text: str) -> str:
    to_block = re.search(
        r"(?:^|\n)\s*TO\s*:?\s*([A-Z][A-Za-z'\-]{1,}(?:\s+[A-Z][A-Za-z'\-]{1,})+)",
        text, re.MULTILINE,
    )
    if to_block:
        return to_block.group(1).strip().title()

    name_match = re.search(r"\b([A-Z][A-Z'\-]{0,}(?:\s+[A-Z][A-Z'\-]{0,})+)\b", text)
    if name_match:
        candidate = name_match.group(1).strip()
        skip_words = {
            "AMAZON", "FEDEX", "UPS", "DHL", "PUROLATOR", "CANADA POST",
            "CANPAR", "INTELCOM", "UNIT", "SUITE", "APT", "STREET", "AVENUE",
            "ROAD", "DRIVE", "BLVD", "RETURN", "SENDER", "RECIPIENT",
        }
        words = candidate.split()
        if not any(w in skip_words for w in words):
            return candidate.title()

    return "UNKNOWN"


def fallback_regex_ocr(image_path: str) -> Dict:
    preprocessed = preprocess_image(image_path)
    text = ""
    try:
        config = r"--oem 3 --psm 6"
        text = pytesseract.image_to_string(preprocessed, config=config).upper()
    except Exception as e:
        log.warning("pytesseract unavailable error=%s", e)
    finally:
        if preprocessed != image_path:
            try:
                os.unlink(preprocessed)
            except OSError:
                pass

    if not text:
        return {
            "unit":        "UNKNOWN",
            "name":        "UNKNOWN",
            "supplier":    "OTHER",
            "parcel_type": guess_parcel_type(image_path),
            "confidence":  "low",
        }

    suppliers_priority = [
        "AMAZON", "UPS", "FEDEX", "UNI", "DRAGONFLY", "EMILE", "FLEETOPTICS",
        "DHL", "PUROLATOR", "INTELCOM", "CANPAR", "CANADA POST",
    ]
    supplier = next((s for s in suppliers_priority if s in text), "OTHER")

    return {
        "unit":        _sanitize(_extract_unit_from_text(text)),
        "name":        _sanitize(_extract_name_from_text(text)),
        "supplier":    _sanitize(supplier),
        "parcel_type": guess_parcel_type(image_path),
        "confidence":  "low",
    }


_GEMINI_PROMPT = """You are reading a shipping/delivery label photo. Your job is to extract key fields from the RECIPIENT (delivery-to) address -- NOT the sender/return address.

Extract and return ONLY a JSON object with exactly these fields:

{
  "unit": "<apartment, suite, or unit number -- typically 3-4 digits, e.g. 204, 1011, 2401>",
  "name": "<recipient's full personal name, e.g. John Smith -- NOT a company name>",
  "supplier": "<one of: AMAZON, UPS, FEDEX, UNI, DRAGONFLY, EMILE, FLEETOPTICS, DHL, PUROLATOR, INTELCOM, CANPAR, CANADA POST, OTHER>",
  "parcel_type": "<see rules below>"
}

Rules for \"unit\":
- The unit/suite number is typically 3-4 digits (e.g. 204, 1011, 2401, 1911).
- Canadian condo addresses often use the format \"UNIT# - STREET# Street Name\". Example: \"2401 - 10 Graphophone Grove\" -> unit is 2401, street number is 10. The unit is the LARGER number BEFORE the dash; the street number is the smaller number AFTER the dash.
- The civic/street numbers for these buildings are \"10\" (10 Graphophone Grove) and \"1285\" (1285 Dupont St). Do NOT return these as the unit.
- If the address contains both a street number and a unit (e.g. \"1285 Dupont St, Suite 204\"), return only the suite/unit portion (204).
- Look for keywords: APT, UNIT, SUITE, #, or a number appearing BEFORE a dash and before the street name.
- Canadian postal codes (e.g. M5V 3A8, M6H 0E5) are NOT unit numbers.
- Include any trailing letter suffix (204A stays 204A).
- If no unit found, use \"UNKNOWN\".

Rules for \"name\":
- Must be a personal name (First Last). NOT a company, building, or courier name.
- Look for prefixes like \"ATTN:\", \"C/O:\", \"Attention:\", or \"Care of:\" -- the name immediately follows.
- If the label shows both a company and a person's name, return the person's name.
- If only a company name is present (no individual), use \"UNKNOWN\".

Rules for \"parcel_type\":
- Amazon shipments in blue poly mailers/bags -> \"PRIME BLUE PACKAGE\"
- Amazon shipments in orange packaging -> \"PRIME ORANGE PACKAGE\"
- Amazon brown cardboard boxes with Prime logo -> \"AMAZON BOX\"
- Any other brown cardboard box -> \"BROWN BOX\"
- Any other brown soft parcel/mailer -> \"BROWN PACKAGE\"
- NEVER use \"bag\" or \"paper bag\" -- use PACKAGE or BOX instead.
- Clear/transparent plastic poly bag or pouch -> \"CLEAR PACKAGE\"
- White opaque polybag or padded mailer -> \"WHITE PACKAGE\"
- Grey polybag or padded mailer -> \"GREY PACKAGE\"
- Only use \"WHITE PACKAGE\" if the bag is visibly opaque white (not see-through).
- Return ONLY the JSON object. No markdown, no explanation."""


def extract_with_gemini(image_path: str) -> Dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")

    preprocessed = preprocess_image(image_path)
    try:
        with open(preprocessed, "rb") as f:
            image_data = base64.b64encode(f.read()).decode()
    finally:
        if preprocessed != image_path:
            try:
                os.unlink(preprocessed)
            except OSError:
                pass

    ext = os.path.splitext(image_path)[1].lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={api_key}"
    )

    payload = {
        "contents": [{
            "parts": [
                {"text": _GEMINI_PROMPT},
                {"inline_data": {"mime_type": mime, "data": image_data}},
            ]
        }],
        "generationConfig": {
            "temperature": 0,
            "topP": 1,
            "topK": 1,
            "maxOutputTokens": 1024,
        },
    }

    log.info("sending image to gemini path=%s", os.path.basename(image_path))
    response = requests.post(url, json=payload, timeout=45)

    if response.status_code != 200:
        raise Exception(f"Gemini API error {response.status_code}: {response.text}")

    result = response.json()
    if not result.get("candidates"):
        raise Exception("No candidates in Gemini response")

    raw = result["candidates"][0]["content"]["parts"][0].get("text", "").strip()
    raw = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()

    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(0))
            return _normalize(data, image_path)
        except json.JSONDecodeError:
            pass

    log.warning("json parse failed salvaging fields from partial gemini output")
    data = {}
    for field in ("unit", "name", "supplier", "parcel_type"):
        m = re.search(rf'"{field}"\s*:\s*"([^"]*)"', raw)
        if m:
            data[field] = m.group(1)
    if not data:
        raise Exception(f"No valid JSON in Gemini output:\n{raw}")
    return _normalize(data, image_path)


def _normalize(data: Dict, image_path: str) -> Dict:
    unit_raw = _sanitize(str(data.get("unit", ""))).upper()
    unit_match = re.search(r"\b(\d{1,5}[A-Z]?)\b", unit_raw)
    unit_candidate = unit_match.group(1) if unit_match else "UNKNOWN"

    _STREET_NUMBERS = {"10", "1285"}
    if unit_candidate in _STREET_NUMBERS:
        unit_candidate = "UNKNOWN"
    if re.fullmatch(r"\d{5}", unit_candidate):
        unit_candidate = "UNKNOWN"

    data["unit"] = unit_candidate

    name = _sanitize(str(data.get("name", "")))
    data["name"] = name.title() if name and name.upper() != "UNKNOWN" else "UNKNOWN"

    valid_suppliers = {
        "AMAZON", "UPS", "FEDEX", "UNI", "DRAGONFLY", "EMILE", "FLEETOPTICS",
        "DHL", "PUROLATOR", "INTELCOM", "CANPAR", "CANADA POST",
    }
    supplier = _sanitize(str(data.get("supplier", "OTHER"))).upper()
    data["supplier"] = supplier if supplier in valid_suppliers else "OTHER"

    data["parcel_type"] = _sanitize(str(data.get("parcel_type", ""))).upper() or guess_parcel_type(image_path)

    return data


_FOCUSED_PROMPT = """Look very carefully at this shipping label image.

I need ONLY these two fields from the DELIVERY/RECIPIENT address block (ignore the return/sender address):

1. The apartment/suite/unit number:
   - Typically 3-4 digits (e.g. 204, 1011, 2401).
   - Canadian condo format: \"UNIT# - STREET# Street Name\" -- e.g. \"2401 - 10 Graphophone Grove\" -> unit is 2401 (before the dash), NOT 10 (the street number after the dash).
   - Building street numbers to reject: \"10\" (10 Graphophone Grove) and \"1285\" (1285 Dupont St).
   - Canadian postal codes (e.g. M5V 3A8, M6H 0E5) are NOT unit numbers.
   - If genuinely not found, return \"UNKNOWN\".

2. The recipient's full personal name (First Last) -- NOT a company name.
   - Check for \"ATTN:\", \"C/O:\", or \"Attention:\" prefixes -- the name follows immediately.
   - If only a company name exists (no individual), return \"UNKNOWN\".

Return ONLY JSON:
{\"unit\": \"<unit number or UNKNOWN>\", \"name\": \"<full name or UNKNOWN>\"}"""


def _retry_focused(image_path: str, current: Dict) -> Dict:
    if current.get("unit") != "UNKNOWN" and current.get("name") != "UNKNOWN":
        return current

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return current

    preprocessed = preprocess_image(image_path)
    try:
        with open(preprocessed, "rb") as f:
            image_data = base64.b64encode(f.read()).decode()
    finally:
        if preprocessed != image_path:
            try:
                os.unlink(preprocessed)
            except OSError:
                pass

    ext = os.path.splitext(image_path)[1].lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={api_key}"
    )

    payload = {
        "contents": [{
            "parts": [
                {"text": _FOCUSED_PROMPT},
                {"inline_data": {"mime_type": mime, "data": image_data}},
            ]
        }],
        "generationConfig": {"temperature": 0, "topP": 1, "topK": 1, "maxOutputTokens": 512},
    }

    try:
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code != 200:
            return current
        result = resp.json()
        if not result.get("candidates"):
            return current
        raw = result["candidates"][0]["content"]["parts"][0].get("text", "").strip()
        raw = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        jm = re.search(r"\{.*?\}", raw, re.DOTALL)
        if not jm:
            return current
        retry_data = json.loads(jm.group(0))

        if current.get("unit") == "UNKNOWN":
            unit_raw = _sanitize(str(retry_data.get("unit", ""))).upper()
            m = re.search(r"\b(\d{1,5}[A-Z]?)\b", unit_raw)
            if m:
                current["unit"] = m.group(1)
                current["confidence"] = "medium"
                log.info("focused retry resolved unit=%s", current["unit"])

        if current.get("name") == "UNKNOWN":
            name = _sanitize(str(retry_data.get("name", "")))
            if name and name.upper() not in ("UNKNOWN", ""):
                current["name"] = name.title()
                log.info("focused retry resolved name=%s", current["name"])

    except Exception as e:
        log.warning("focused retry failed error=%s", e)

    return current


def extract_data(image_path: str, building: str = "") -> Dict:
    """
    Unified extraction: Gemini first, focused retry if UNKNOWN fields remain, pytesseract fallback.
    confidence: high = Gemini first pass, medium = focused retry resolved a field, low = pytesseract fallback.
    """
    log.info("analyzing image=%s building=%s", os.path.basename(image_path), building)

    try:
        result = extract_with_gemini(image_path)
        result["confidence"] = "high"
    except Exception as e:
        log.warning("gemini failed error=%s using ocr fallback", e)
        result = fallback_regex_ocr(image_path)

    result = _retry_focused(image_path, result)

    for key in ["unit", "name", "supplier", "parcel_type"]:
        if not result.get(key) or result[key] == "UNKNOWN":
            log.warning("field still unknown key=%s running ocr fallback", key)
            backup = fallback_regex_ocr(image_path)
            if backup.get(key) and backup[key] != "UNKNOWN":
                result[key] = backup[key]
                if result.get("confidence") == "high":
                    result["confidence"] = "low"

    if building and result.get("unit") not in (None, "UNKNOWN"):
        result["unit"] = _validate_unit(result["unit"], building)

    log.info(
        "extraction complete unit=%s name=%s supplier=%s confidence=%s",
        result.get("unit"), result.get("name"), result.get("supplier"),
        result.get("confidence", "low"),
    )

    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python ocr_utils.py <image_path> [building]")
        sys.exit(1)
    path = sys.argv[1]
    bld = sys.argv[2] if len(sys.argv) > 2 else ""
    if not os.path.exists(path):
        print(f"File not found: {path}")
        sys.exit(1)
    result = extract_data(path, building=bld)
    print(json.dumps(result, indent=2))

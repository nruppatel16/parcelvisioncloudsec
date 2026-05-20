import os
import sys

BUILDING_IDS = ["G1", "G2"]

SHEET_ID_G1  = os.getenv("SHEET_ID_G1", "")
SHEET_ID_G2  = os.getenv("SHEET_ID_G2", "")
WORKSHEET_G1 = os.getenv("WORKSHEET_G1", "PACKAGES NEW")
WORKSHEET_G2 = os.getenv("WORKSHEET_G2", "PACKAGES NEW")

API_KEY               = os.getenv("API_KEY", "")
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
MAX_UPLOAD_SIZE_MB    = int(os.getenv("MAX_UPLOAD_SIZE_MB", "10"))
GEMINI_API_KEY        = os.getenv("GEMINI_API_KEY", "")

ENV       = os.getenv("ENV", "development")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

_REQUIRED_IN_PRODUCTION = {
    "SHEET_ID_G1":    SHEET_ID_G1,
    "SHEET_ID_G2":    SHEET_ID_G2,
    "API_KEY":        API_KEY,
    "GEMINI_API_KEY": GEMINI_API_KEY,
}

if ENV == "production":
    missing = [k for k, v in _REQUIRED_IN_PRODUCTION.items() if not v]
    if missing:
        sys.exit(f"Missing required environment variables: {', '.join(missing)}")

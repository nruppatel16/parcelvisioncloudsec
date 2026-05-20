import os
import time
import logging

import gspread
from oauth2client.service_account import ServiceAccountCredentials

import config

log = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]


def _credentials_path(building: str) -> str:
    if building == "G1":
        return os.path.join(BASE_DIR, os.getenv("G1_CREDENTIALS_PATH", "credentials_g1.json"))
    return os.path.join(BASE_DIR, os.getenv("CREDENTIALS_PATH", "credentials.json"))


def connect_to_sheet(building: str = "G2"):
    creds = ServiceAccountCredentials.from_json_keyfile_name(_credentials_path(building), _SCOPE)
    client = gspread.authorize(creds)
    if building == "G1":
        return client.open_by_key(config.SHEET_ID_G1).worksheet(config.WORKSHEET_G1)
    return client.open_by_key(config.SHEET_ID_G2).worksheet(config.WORKSHEET_G2)


def append_row(row_data: list, building: str = "G2"):
    """
    Append a parcel entry to the building's Google Sheet.
    Retries up to 3 times with exponential backoff on gspread API failures.
    row_data: [timestamp, unit, name, supplier, parcel_type, released?, released_time]
    """
    if not isinstance(row_data, list) or len(row_data) != 7:
        raise ValueError(f"row_data must be a list of 7 elements, got {len(row_data)}")

    building = building.upper()
    last_error = None

    for attempt in range(3):
        try:
            sheet = connect_to_sheet(building)
            col_a = sheet.col_values(1)
            next_row = (len(col_a) or 1) + 1
            sheet.insert_row(row_data, index=next_row, value_input_option="USER_ENTERED")
            log.info("sheet row written building=%s row=%d", building, next_row)
            return row_data
        except Exception as e:
            last_error = e
            wait = 2 ** attempt
            log.warning("sheet write failed attempt=%d wait=%ds error=%s", attempt + 1, wait, e)
            if attempt < 2:
                time.sleep(wait)

    raise RuntimeError(f"Sheet write failed after 3 attempts: {last_error}")


def get_last_entry(building: str = "G2") -> dict | None:
    sheet = connect_to_sheet(building.upper())
    values = sheet.get_all_values()
    if not values or len(values) <= 1:
        return None
    return dict(zip(values[0], values[-1]))

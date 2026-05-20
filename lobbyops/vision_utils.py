from ocr_utils import extract_data


def analyze_parcel(image_path: str, building: str = "") -> dict:
    return extract_data(image_path, building=building)

import os
import threading
import uuid
import logging
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

import config
import queue_store
from auth import require_api_key, validate_upload_size
from vision_utils import analyze_parcel
from sheet_utils import append_row

from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(module)s %(message)s",
)
log = logging.getLogger(__name__)

app = Flask(__name__)

if config.ENV == "production":
    CORS(app, origins=["https://my.1valetbas.com"])
else:
    CORS(app)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[f"{config.RATE_LIMIT_PER_MINUTE} per minute"],
    storage_uri="memory://",
)

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

_start_time = datetime.now(timezone.utc)
_job_results: dict = {}

queue_store.init_db()


@app.before_request
def _attach_request_id():
    request.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex


@app.after_request
def _set_request_id_header(response):
    response.headers["X-Request-ID"] = getattr(request, "request_id", "")
    return response


@app.route("/health")
def health():
    uptime = (datetime.now(timezone.utc) - _start_time).total_seconds()
    queue_status = {b: queue_store.get_metrics(b) for b in config.BUILDING_IDS}
    return jsonify({"uptime_seconds": uptime, "queue": queue_status})


@app.route("/metrics")
def metrics():
    return jsonify({b: queue_store.get_metrics(b) for b in config.BUILDING_IDS})


@app.route("/upload", methods=["POST"])
@validate_upload_size
def upload_parcel():
    rid = request.request_id
    log.info("upload start request_id=%s", rid)

    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    building = request.form.get("building", "G2").upper()
    if building not in config.BUILDING_IDS:
        building = "G2"

    job_id    = uuid.uuid4().hex
    temp_path = os.path.join(UPLOAD_FOLDER, f"tmp_{job_id}.jpg")
    file.save(temp_path)
    _job_results[job_id] = {"status": "processing"}

    def process(job_id, temp_path, building, rid):
        try:
            log.info("ocr start job=%s building=%s request_id=%s", job_id, building, rid)
            result = analyze_parcel(temp_path, building=building)
            if isinstance(result, list):
                result = result[0] if result else {}

            unit        = str(result.get("unit",        "UNKNOWN")).strip().upper()
            name        = str(result.get("name",        "UNKNOWN")).strip().upper()
            supplier    = str(result.get("supplier",    "OTHER")).strip().upper()
            parcel_type = str(result.get("parcel_type", "BROWN BOX")).strip().upper()
            confidence  = result.get("confidence", "low")

            log.info(
                "ocr result job=%s unit=%s name=%s confidence=%s request_id=%s",
                job_id, unit, name, confidence, rid,
            )

            ts_readable = datetime.now().strftime("%m/%d/%Y %H:%M:%S")
            ts_safe     = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

            append_row([ts_readable, unit, name, supplier, parcel_type, "", ""], building=building)
            log.info("sheets written job=%s building=%s request_id=%s", job_id, building, rid)

            safe_name = (
                f"{ts_safe}_{building}_{unit}_{name}_{supplier}_{parcel_type}.jpg"
                .replace(" ", "_")
                .replace("/", "-")
            )
            final_path = os.path.join(UPLOAD_FOLDER, safe_name)
            os.rename(temp_path, final_path)

            if not unit or unit == "UNKNOWN":
                log.warning("unit unknown job=%s name=%s request_id=%s", job_id, name, rid)
                _job_results[job_id] = {
                    "status":         "review_required",
                    "data":           {"unit": unit, "name": name,
                                       "supplier": supplier, "parcel_type": parcel_type},
                    "confidence":     confidence,
                    "building":       building,
                    "image_saved_as": safe_name,
                }
                return

            queue_store.enqueue(building, unit, name, supplier, parcel_type)
            log.info("queued job=%s unit=%s building=%s request_id=%s", job_id, unit, building, rid)

            _job_results[job_id] = {
                "status":         "success",
                "message":        "Parcel processed",
                "image_saved_as": safe_name,
                "data":           {"unit": unit, "name": name,
                                   "supplier": supplier, "parcel_type": parcel_type},
                "confidence":     confidence,
                "sheets_status":  "success",
                "valet_status":   "queued",
                "building":       building,
            }

        except Exception as e:
            log.exception("process error job=%s request_id=%s", job_id, rid)
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            _job_results[job_id] = {"status": "error", "error": str(e)}

    threading.Thread(
        target=process,
        args=(job_id, temp_path, building, rid),
        daemon=True,
    ).start()

    return jsonify({"status": "processing", "job_id": job_id}), 202


@app.route("/result/<job_id>", methods=["GET"])
def get_job_result(job_id):
    result = _job_results.get(job_id)
    if result is None:
        return jsonify({"status": "not_found"}), 404
    if result.get("status") in ("success", "error", "review_required"):
        _job_results.pop(job_id, None)
    return jsonify(result)


@app.route("/valet/pending", methods=["GET"])
@require_api_key
def get_pending_units():
    building = request.args.get("building", "G2").upper()
    if building not in config.BUILDING_IDS:
        building = "G2"
    units = queue_store.get_pending(building)
    if not units:
        return jsonify({"status": "empty", "units": []})
    return jsonify({"status": "pending", "count": len(units), "units": units})


@app.route("/valet/complete", methods=["POST"])
@require_api_key
def mark_unit_complete():
    data     = request.get_json() or {}
    unit     = data.get("unit", "")
    success  = data.get("success", False)
    building = data.get("building", "G2").upper()
    if building not in config.BUILDING_IDS:
        building = "G2"
    rid = request.request_id

    if success:
        queue_store.mark_complete(building, unit)
        log.info("unit complete unit=%s building=%s request_id=%s", unit, building, rid)
        m = queue_store.get_metrics(building)
        return jsonify({
            "status":  "success",
            "message": f"Unit {unit} marked complete",
            "pending": m["pending_count"],
        })

    queue_store.increment_retry(building, unit)
    log.warning("unit failed unit=%s building=%s request_id=%s", unit, building, rid)
    return jsonify({"status": "error", "message": "Unit add failed, retry count incremented"}), 400


@app.route("/valet/flag", methods=["POST"])
@require_api_key
def flag_unit():
    data     = request.get_json() or {}
    unit     = data.get("unit", "")
    building = data.get("building", "G2").upper()
    if building not in config.BUILDING_IDS:
        building = "G2"
    queue_store.mark_failed(building, unit)
    log.warning("unit flagged unit=%s building=%s request_id=%s", unit, building, request.request_id)
    return jsonify({"status": "flagged", "unit": unit})


@app.route("/valet/flagged", methods=["GET"])
@require_api_key
def get_flagged_units():
    building = request.args.get("building", "G2").upper()
    if building not in config.BUILDING_IDS:
        building = "G2"
    items = queue_store.get_flagged(building)
    return jsonify({"building": building, "count": len(items), "items": items})


@app.route("/valet/queue-status", methods=["GET"])
@require_api_key
def queue_status():
    building = request.args.get("building", "G2").upper()
    if building not in config.BUILDING_IDS:
        building = "G2"
    units = queue_store.get_pending(building)
    return jsonify({
        "building":      building,
        "queue_size":    len(units),
        "pending_units": [u["unit"] for u in units],
    })


@app.route("/valet/clear-queue", methods=["POST"])
@require_api_key
def clear_queue():
    data     = request.get_json(silent=True) or {}
    building = data.get("building", request.args.get("building", "G2")).upper()
    if building not in config.BUILDING_IDS:
        building = "G2"
    # Mark pending as failed rather than deleting rows, so metrics stay accurate
    units = queue_store.get_pending(building)
    for u in units:
        queue_store.mark_failed(building, u["unit"])
    log.info("queue cleared building=%s count=%d request_id=%s",
             building, len(units), request.request_id)
    return jsonify({"status": "success", "cleared": len(units)})


if __name__ == "__main__":
    import socket
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "localhost"
    log.info("starting lobbyops host=%s port=5002", local_ip)
    app.run(host="0.0.0.0", port=5002, debug=(config.ENV != "production"))

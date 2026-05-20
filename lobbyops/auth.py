import hmac
import functools
from flask import request, jsonify
from config import API_KEY, MAX_UPLOAD_SIZE_MB


def require_api_key(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        provided = request.headers.get("X-API-Key", "")
        # hmac.compare_digest prevents timing attacks on key comparison
        if not hmac.compare_digest(provided.encode(), API_KEY.encode()):
            return jsonify({}), 401
        return f(*args, **kwargs)
    return decorated


def validate_upload_size(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        max_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
        length = request.content_length
        if length is not None and length > max_bytes:
            return jsonify({"error": "Payload too large"}), 413
        return f(*args, **kwargs)
    return decorated

# Security

## Authentication model

All `/valet/*` routes require an `X-API-Key` header. The key is compared using `hmac.compare_digest`, which runs in constant time regardless of where the strings diverge. A standard string equality check would allow an attacker to infer the correct key one byte at a time by measuring response latency. `hmac.compare_digest` eliminates that channel.

On failure the endpoint returns HTTP 401 with an empty JSON body. No detail is given -- not "key missing", not "key invalid". The distinction is irrelevant to legitimate callers and useful to attackers.

The `/upload`, `/health`, and `/metrics` endpoints do not require authentication. `/upload` is rate-limited and size-bounded. `/health` and `/metrics` expose no secrets -- only counts and uptime.

## Transport security

nginx terminates TLS on port 443. Flask (via gunicorn) listens on `127.0.0.1:5002` and is not reachable from outside the instance. The security group has no inbound rule for port 5002. Port 80 is not open.

HSTS is set with a one-year max-age and `includeSubDomains`. This prevents protocol downgrade attacks after the first connection.

TLS 1.0 and 1.1 are disabled. The cipher suite excludes anonymous and MD5 variants.

## Input handling

**File size:** The `validate_upload_size` decorator checks `Content-Length` before the request body is read. Requests exceeding `MAX_UPLOAD_SIZE_MB` (default 10 MB) return 413 immediately.

**Control characters:** Every string returned by the extraction pipeline passes through `_sanitize()` in `ocr_utils.py`, which strips ASCII control characters (0x00-0x1F, 0x7F) and Unicode C1 control characters (0x80-0x9F). This prevents log injection and ensures clean data reaches the database and Google Sheets.

**Unit allowlist:** Extracted unit numbers are validated against a per-building allowlist (`units_g1.txt`, `units_g2.txt`) using `difflib.get_close_matches` with a cutoff of 0.85. An exact match passes unchanged. A close match is corrected and logged. A value with no match at or above the cutoff passes through unmodified -- the allowlist corrects OCR noise, it does not gate entry. Parcels with unit `UNKNOWN` after all extraction passes return `review_required` and are not enqueued.

**MIME validation:** File type is inferred from extension. Gemini receives the correct MIME type. Malformed images cause the Gemini call to fail, which triggers the pytesseract fallback. The file is saved to a local uploads directory regardless.

## Rate limiting

Two independent layers:

1. **nginx:** 10 requests/second per IP, burst of 20, enforced via `limit_req_zone`. Excess requests receive 429 before reaching the application.

2. **Flask-Limiter:** 30 requests/minute per IP (configurable via `RATE_LIMIT_PER_MINUTE`). Applied at the application layer as a second fence in case nginx is bypassed or misconfigured.

The nginx limit handles burst protection. The Flask limit handles sustained abuse that stays below the burst threshold.

## Secrets management

On EC2, secrets are stored in AWS SSM Parameter Store under the `/lobbyops/` path hierarchy and pulled into `.env` during instance bootstrap via the `user_data.sh` script. The IAM role attached to the instance has `ssm:GetParametersByPath` scoped to `/lobbyops/*` only -- no other SSM paths, no S3, no other AWS APIs.

The `.env` file is written with mode 600, owned by the `lobbyops` service user. It is not committed to source control. `.env` is listed in `.gitignore`.

The `GEMINI_API_KEY` and `API_KEY` values are never logged. The `API_KEY` is never returned in any API response.

## Audit trail

Every upload generates a structured log line with job ID, building, unit, name, and confidence level. Every queue state transition (enqueue, complete, failed, flagged) generates a log line with unit, building, and request ID.

All parcel events are written to SQLite (`/data/lobbyops.db`) on the EBS volume. The EBS volume is encrypted at rest. SQLite provides a durable event log that survives application restarts and can be queried directly for audit purposes.

Google Sheets receives a copy of every parcel event, including timestamp, unit, name, supplier, and type. The sheet serves as an off-instance record visible to property management.

## CDP attack surface

The Chrome DevTools Protocol socket is bound to `localhost:9222` on the lobby workstation. It is not forwarded through the server or exposed externally. `inject_tab.py` is a one-shot script run manually from the workstation when the 1Valet tab needs to be reinjected (e.g., after a browser restart). The injected script has no elevated permissions beyond what the 1Valet page itself has.

The script communicates with the backend over HTTPS using the same `API_KEY` as any other client. An attacker with local access to the workstation could read the injected script from Chrome's memory, but they already have the credentials needed to interact with the API directly.

## Known limitations and mitigations

| Limitation | Mitigation |
|---|---|
| In-process job result store (`_job_results` dict) is lost on restart | Results are short-lived (seconds); clients poll until terminal state then discard. No data loss in practice. |
| Single SQLite file on EBS | EBS is replicated within the AZ. For cross-AZ durability, take EBS snapshots via AWS Backup. |
| API key is a shared secret across all clients | Rotate via `scripts/rotate_key.sh` on any suspected exposure. Use separate keys per client if the attack surface grows. |
| Google Sheets write failures are retried 3 times then raise | The parcel is still logged in SQLite and queued for 1Valet. Sheets is a secondary log, not the primary record. |
| pytesseract fallback requires tesseract installed on the host | If tesseract is absent, fallback returns UNKNOWN for text fields. Gemini handles the majority of extractions. |

## Key rotation procedure

1. Run `bash /opt/lobbyops/scripts/rotate_key.sh` on the instance.
2. The script generates a new 32-byte hex key, updates `.env` in place, and restarts the service.
3. The new key is printed to stdout once.
4. Update the key in all clients: the iOS Shortcut that POSTs to `/upload`, and any monitoring scripts that call `/valet/*`.
5. Update the key in SSM Parameter Store so the next instance launch picks up the new value.

Rotate at least every 90 days, at any personnel offboarding, and immediately on suspected compromise.

## Vulnerability disclosure

Report security issues to the repository owner via GitHub private vulnerability reporting. Do not file public issues for unpatched vulnerabilities.

# Architecture

## System overview

LobbyOps is a forward-deployed automation system that handles parcel intake for two residential buildings from a single Flask backend. When a package arrives, staff photograph the shipping label using a mobile shortcut. The image is sent to the backend, which extracts the unit number and recipient name using the Gemini Vision API, writes the event to Google Sheets and SQLite, and places the unit in a building-scoped queue. A JavaScript listener running inside the property management portal (1Valet) polls the queue and types each unit into the active delivery form, completing the intake without manual keyboard input.

The system runs in production on a single EC2 t3.micro instance behind nginx. Both buildings (Galleria 1 and Galleria 2) share the same backend process, with data isolated at the queue and database level by a `building` field.

## Component diagram

```
[Mobile Camera / iOS Shortcut]
          |
          | HTTPS POST /upload
          v
  [nginx :443 -- TLS termination, rate limiting, security headers]
          |
          | proxy_pass to 127.0.0.1:5002
          v
  [gunicorn -- 2 workers]
          |
          v
       [app.py]
          |
          +-----------> [ocr_utils.py]
          |                   |
          |                   +--> [Gemini 2.5 Flash API]  (HTTPS, primary)
          |                   |       on UNKNOWN fields:
          |                   +--> [Gemini focused retry]  (HTTPS, second pass)
          |                   |
          |                   +--> [pytesseract]           (local, fallback)
          |
          +-----------> [sheet_utils.py] --> [Google Sheets API]  (HTTPS)
          |
          +-----------> [queue_store.py] --> [SQLite: /data/lobbyops.db]

  [inject_tab.py]  (CLI, run once on lobby workstation)
          |
          | WebSocket -- Chrome DevTools Protocol (localhost:9222)
          v
  [Chrome: 1Valet portal tab]
          |
          | [smartlockerscript.js] polls every 5s
          |
          +-- GET /valet/pending?building=G2
          +-- POST /valet/complete
          +-- POST /valet/flag
          |
          v
  [1Valet DOM -- suite number input field]
```

## Data flow

1. **Capture:** Staff photograph a shipping label. An iOS Shortcut sends a multipart POST to `/upload` with the image file and a `building` field (G1 or G2).

2. **Job dispatch:** `app.py` saves the image to the uploads directory, creates a job ID, stores `{status: processing}` in the in-memory job results dict, and launches a daemon thread to handle extraction. Returns HTTP 202 with the job ID.

3. **Extraction (ocr_utils.py):** The background thread calls `extract_data(image_path, building)`. Gemini 2.5 Flash receives the preprocessed image and the extraction prompt. If Gemini succeeds, confidence is set to `high`. If any field is `UNKNOWN`, a focused retry call targets only unit and name. If the retry resolves a field, confidence becomes `medium`. If Gemini fails entirely, pytesseract runs as fallback and confidence is `low`.

4. **Unit validation (ocr_utils.py):** The extracted unit is checked against the building's allowlist file using `difflib.get_close_matches` at cutoff 0.85. Close matches are corrected and logged. This catches common OCR errors like `204` vs `204A`.

5. **Sheets write (sheet_utils.py):** `append_row` connects to the building's Google Sheet using the corresponding service account credentials, finds the next empty row, and inserts the parcel record. Retries up to 3 times with exponential backoff (1s, 2s, 4s) on API failures.

6. **Queue write (queue_store.py):** If the unit is not `UNKNOWN`, `enqueue` inserts a row into the `parcels` SQLite table with status `pending`. If the unit is `UNKNOWN`, the job result is set to `review_required` with the extracted data returned for human correction, and nothing is enqueued.

7. **Job result:** The job result dict is updated. The client polls `GET /result/<job_id>` until it receives a terminal status (`success`, `error`, or `review_required`).

8. **1Valet automation (smartlockerscript.js):** The listener polls `GET /valet/pending?building=G2` every 5 seconds. On a non-empty response, it takes the first item, types the unit number into the suite input field using simulated DOM events, clicks the matching dropdown entry (or dispatches Enter if no dropdown appears), then calls `POST /valet/complete` with `success: true`. The backend marks the row complete and records `completed_at`.

## Multi-building design

G1 (Galleria 1, 10 Graphophone Grove) and G2 (Galleria 2, 1285 Dupont St) share one Flask process, one SQLite database, and one nginx instance. Data isolation is enforced by the `building` column in the `parcels` table and by all query functions requiring `building` as a parameter.

Each building has its own:
- Google Sheet and service account credentials
- Unit allowlist file (`units_g1.txt`, `units_g2.txt`)
- Chrome tab with a separately injected script (run `inject_tab.py` with `G1` or `G2`)
- `BUILDING` constant baked into the injected script at injection time

The Gemini prompts reference both building addresses explicitly, so the model can resolve the Canadian condo address format (e.g., `2401 - 10 Graphophone Grove` where 2401 is the unit and 10 is the street number) correctly for each building.

## CDP injection mechanism

`inject_tab.py` connects to Chrome's DevTools endpoint at `localhost:9222` via HTTP to enumerate open tabs, then opens a WebSocket to the target tab's `webSocketDebuggerUrl`. It sends a `Runtime.evaluate` CDP message containing the full contents of `smartlockerscript.js`, with `__SERVER_URL__` and `__BUILDING__` replaced by the actual values before sending.

Chrome evaluates the script in the context of the active page. The script wraps itself in an IIFE and registers `startValetListener`, `stopValetListener`, `valetStatus`, `valetClearCache`, and `addUnit` on `window`. These are available in the browser console for manual control.

The listener auto-starts 3 seconds after injection, giving staff time to confirm the ADD DELIVERY popup is open. If the popup is not open, `startListener` logs an error and returns without starting the poll loop. Staff can open the popup and call `startValetListener()` from the console.

The WebSocket connection used for injection is closed immediately after the `Runtime.evaluate` response is received. The injected script communicates with the backend via standard `fetch` calls, not via CDP.

## Queue lifecycle

Each parcel record moves through the following states:

```
pending
  |
  +-- addUnit succeeds  --> complete
  |
  +-- addUnit fails     --> increment_retry
                               |
                               +-- retry_count < 3  --> pending (stays)
                               |
                               +-- retry_count >= 3 --> flagged
  |
  +-- /valet/flag POST        --> failed
```

`flagged` items appear in `GET /valet/flagged` for manual review. They are not retried automatically. Staff can manually enter the unit in 1Valet and clear the flag by calling the appropriate endpoint.

`failed` items result from explicit `POST /valet/flag` calls (triggered by the JavaScript client after exhausting its own retry budget of 3 attempts at 2s/4s/8s delays) or from the clear-queue operation.

Metrics (`GET /metrics`) report today's total, today's complete, current pending, current failed, and average processing time in seconds.

## Failure modes and recovery

**Gemini outage:** `extract_with_gemini` raises, `extract_data` catches and falls back to pytesseract. If tesseract is installed, extraction continues at lower confidence. If tesseract is absent, all fields return `UNKNOWN`. Parcels with `UNKNOWN` unit return `review_required` to the client; they are not enqueued and not lost -- the image is saved to disk.

**Google Sheets outage:** `append_row` retries 3 times with exponential backoff, then raises. The background thread catches the exception, logs it, and sets the job result to `error`. The parcel is not enqueued. The image is saved. Staff can manually append the row to the sheet using data from the error response.

**Chrome tab closed or 1Valet session expired:** The polling loop catches the fetch error and logs it. Items stay `pending` in the queue indefinitely. When staff reopen 1Valet and reinject the script (`python inject_tab.py`), the listener picks up the pending queue immediately on the next poll cycle.

**Instance restart:** The in-memory job results dict is cleared. Any in-flight uploads (between 202 and terminal state) appear as `not_found` on the next poll. The SQLite database on the EBS volume is durable -- all committed parcel records survive. gunicorn is managed by systemd with `Restart=always`, so the service comes back automatically.

## Cloud deployment topology

```
[Internet]
    |
    | 443 (HTTPS)
    v
[Elastic IP] --> [EC2 t3.micro, Amazon Linux 2023]
                    |
                    +-- nginx (443 -> 127.0.0.1:5002)
                    |     TLS via Let's Encrypt
                    |
                    +-- gunicorn (127.0.0.1:5002)
                    |     2 workers, lobbyops user
                    |
                    +-- /data/lobbyops.db
                    |     EBS gp3 20GB, encrypted
                    |
                    +-- IAM instance role
                          SSM GetParametersByPath /lobbyops/*
```

External dependencies (all via HTTPS on port 443):
- Gemini API (`generativelanguage.googleapis.com`)
- Google Sheets API (`sheets.googleapis.com`)
- 1Valet portal (`my.1valetbas.com`) -- inbound requests only; the server does not initiate connections to 1Valet

Terraform manages the EC2 instance, EBS volume, Elastic IP, IAM role and instance profile, and security group. State should be stored in an S3 backend with DynamoDB locking for production use.

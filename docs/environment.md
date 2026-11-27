# Runtime configuration

Start with [the example environment file](../.env.example) and follow the [service setup guide](service-setup.md). The backend reads its environment at startup. Restart or redeploy after changes to Render Environment. Flutter fetches public settings from the backend when an upload or sign-in flow opens. Changing the file size or policy count limits requires only a backend restart and reopening the flow.

Boolean values accept true or false. Numeric limits and enum patterns are validated at startup. Blank optional credentials disable their integrations unless an enabled provider requires them. Historical static/console OTP provider values resolve to disabled. Set OTP_LENGTH to at least six before rollout. Outstanding legacy OTPs need to be requested again.

| Variable | Default | Purpose and limits |
|---|---|---|
| `PORT` | `8000` | Server port |
| `ENVIRONMENT` | `development` | development \| production |
| `SUPPORT_EMAIL` | Empty | Help & Support contact shown in the app (served by GET /api/app-config) |
| `JWT_SECRET` | Required | A nonempty HS256 signing secret. Use a strong random value and preserve the existing key to keep active sessions valid. |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Access token lifetime in minutes, minimum 1. |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `365` | Sliding refresh token lifetime in days, minimum 1. |
| `ALLOWED_ORIGINS` | Empty | Comma-separated browser origins. Defaults to http://localhost:8000 when unset. |
| `GOOGLE_CLIENT_ID` | Empty | Public Google Web OAuth client ID |
| `OTP_LENGTH` | `6` | Digits in a generated OTP (minimum 6, maximum 8) |
| `OTP_EXPIRE_SECONDS` | `300` | OTP validity (minimum 60) |
| `OTP_MAX_ATTEMPTS` | `5` | Wrong tries before the OTP is discarded (minimum 1) |
| `OTP_RESEND_COOLDOWN_SECONDS` | `60` | Min gap between OTP requests (minimum 1) |
| `OTP_HASH_SECRET` | Empty | Separate OTP signing secret, defaults to JWT_SECRET |
| `OTP_SEND_RATE_LIMIT` | `5/minute` | Per-IP OTP send rate limit |
| `OTP_VERIFY_RATE_LIMIT` | `10/minute` | Per-IP OTP and sign-in verification rate limit |
| `AUTH_REQUEST_TIMEOUT_SECONDS` | `10` | Authentication network timeout (minimum 1, maximum 30) |
| `EMAIL_OTP_PROVIDER` | `disabled` | Use `smtp_oauth` for Microsoft 365 on a network that allows port 587. Hosted HTTPS options include `gmail` and `resend`. |
| `SMTP_TENANT_ID`, `SMTP_CLIENT_ID`, `SMTP_CLIENT_SECRET` | Empty | Microsoft Entra client credentials for `smtp_oauth`. Add only to the backend environment. |
| `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, `GMAIL_FROM_ADDRESS` | Empty | OAuth sender settings required for `gmail`. |
| `EMAIL_API_KEY` | Empty | Resend sending API key |
| `EMAIL_FROM_ADDRESS` | Empty | Resend verified sender address |
| `OTP_EMAIL_SUBJECT` | `Your {app} verification code` | Email subject; {code}, {minutes} and {app} are filled in |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server host |
| `SMTP_PORT` | `587` | SMTP port (587 = STARTTLS, 465 = SSL) |
| `SMTP_USERNAME` | Empty | Mailbox used to authenticate to SMTP |
| `SMTP_PASSWORD` | Empty | Password for the legacy `smtp` provider |
| `SMTP_FROM_EMAIL` | Empty | Sender address; defaults to SMTP_USERNAME |
| `SMTP_FROM_NAME` | `Policy Pal` | Sender display name / app name in emails |
| `SMTP_TIMEOUT_SECONDS` | `15` | SMTP connect/send timeout (minimum 1) |
| `MONGODB_URI` | Required | MongoDB connection URI. The historical MONGO_URL alias is accepted. |
| `MONGODB_DATABASE` | Empty | Use an explicit database name. If blank, use the URI database or claimsupport. |
| `GEMINI_API_KEY` | Required | Server-side key for Gemini extraction and coverage analysis. |
| `AI_MODEL` | `models/gemini-flash-lite-latest` | Gemini model for AI analysis |
| `AI_MAX_ATTEMPTS` | `3` | Total attempts per LLM generation request, including the first call |
| `AI_MAX_CONCURRENT_REQUESTS` | `3` | Max simultaneous in-flight Gemini requests across the process (minimum 1, maximum 20) |
| `MAX_POLICIES_PER_ANALYSIS` | `3` | Max policies allowed in a single multi-policy analysis request (minimum 1, maximum 5) |
| `MULTI_POLICY_MAX_CONCURRENCY` | `2` | Max policy analyses processed concurrently within one multi-policy session (minimum 1, maximum 10) |
| `LOG_LEVEL` | `INFO` | One of TRACE, DEBUG, INFO, SUCCESS, WARNING, ERROR or CRITICAL. |
| `SENTRY_ENABLED` | `false` | Enable privacy-filtered backend error tracking |
| `SENTRY_DSN` | Empty | Sentry project DSN |
| `SENTRY_TRACES_SAMPLE_RATE` | `0` | Trace sampling fraction (minimum 0, maximum 1) |
| `SENTRY_ERROR_SAMPLE_RATE` | `1` | Error sampling fraction (minimum 0, maximum 1) |
| `MAX_FILE_SIZE_MB` | `5` | Max upload file size in MiB (minimum 1, maximum 10) |
| `ALLOWED_MIME_TYPES` | `application/pdf,image/png,image/jpeg,application/vnd.openxmlformats-officedocument.wordprocessingml.document` | Comma-separated allowed MIME types |
| `PDF_COMPRESSION_ENABLED` | `true` | Optimize eligible PDF scan images in GridFS. |
| `PDF_COMPRESSION_DPI` | `150` | Target scan DPI for PDF compression. (minimum 72, maximum 300) |
| `PDF_GRAYSCALE_ENABLED` | `true` | Allow grayscale when the scan has no material color information. |
| `PDF_COMPRESSION_JPEG_QUALITY` | `85` | JPEG quality for eligible scanned images. (minimum 70, maximum 95) |
| `PDF_COMPRESSION_MAX_PAGE_PIXELS` | `10000000` | Pixel budget for each candidate page image. (minimum 1000000, maximum 25000000) |
| `PDF_COMPRESSION_MAX_PAGES` | `100` | Skip optional optimization for PDFs above this page count. (minimum 1, maximum 1000) |
| `FILE_DEDUPLICATION_ENABLED` | `true` | Reuse identical physical bytes by original SHA-256. Each upload gets an opaque owner-bound handle. |
| `ORPHAN_CLEANUP_ENABLED` | `true` | Clean up verified unlinked uploads and orphaned GridFS content. |
| `ORPHAN_CLEANUP_INTERVAL_HOURS` | `24` | Hours between orphan cleanup runs. (minimum 1, maximum 168) |
| `ORPHAN_CLEANUP_GRACE_HOURS` | `24` | Minimum age for an orphan cleanup candidate. (minimum 1, maximum 720) |
| `MAINTENANCE_CHECK_INTERVAL_MINUTES` | `15` | Minutes between checks for due maintenance tasks. (minimum 1, maximum 60) |
| `STORAGE_MONITOR_ENABLED` | `true` | Enable periodic storage measurement. |
| `STORAGE_MONITOR_INTERVAL_HOURS` | `24` | Hours between storage measurements. (minimum 1, maximum 168) |
| `STORAGE_CAPACITY_BYTES` | `536870912` | Quota denominator in bytes. Set this to match the Atlas tier. (> 0) |
| `STORAGE_WARNING_THRESHOLD_PERCENT` | `80` | Warning as a percentage of storage capacity, below the critical threshold. (minimum 1, maximum 100) |
| `STORAGE_CRITICAL_THRESHOLD_PERCENT` | `90` | Critical percent of storage capacity. (minimum 1, maximum 100) |
| `DATA_CLEANUP_DRY_RUN` | `false` | Run cleanup without deleting |
| `DATA_CLEANUP_MAX_PERCENTAGE` | `0.05` | Max percentage to delete per run (minimum 0.01, maximum 1.0) |
| `DATA_CLEANUP_MAX_COUNT` | `10000` | Maximum orphan candidates per run (minimum 1) |
| `OCR_DPI` | `150` | Scan render resolution, subject to the pixel budget. (minimum 72, maximum 300) |
| `OCR_LANGUAGE` | `eng` | Installed Tesseract language code, or codes joined by +. (pattern ^[a-zA-Z0-9_]+(`:\+[a-zA-Z0-9_]+)*$) |
| `OCR_PREPROCESS_ENABLED` | `true` | Enable OpenCV noise reduction and contrast enhancement. |
| `OCR_DESKEW_ENABLED` | `true` | Correct small detected rotations when preprocessing. |
| `OCR_THRESHOLD_ENABLED` | `false` | Apply Otsu binarization when preprocessing. |
| `OCR_MIN_CONFIDENCE` | `50` | Minimum mean Tesseract confidence on a scanned page. (minimum 0, maximum 100) |
| `OCR_TIMEOUT_SECONDS` | `90` | Maximum extraction time in seconds for a PDF or image. (minimum 1, maximum 300) |
| `OCR_QUEUE_TIMEOUT_SECONDS` | `15` | Maximum seconds to wait for the OCR worker. (minimum 1, maximum 60) |
| `OCR_MAX_SCANNED_PAGES` | `30` | Maximum scan pages per PDF. Text-only pages do not count. (minimum 1, maximum 200) |
| `OCR_MAX_IMAGE_PIXELS` | `8000000` | Maximum decoded image pixels for OCR. (minimum 1000000, maximum 20000000) |
| `TESSERACT_CMD` | `tesseract` | Absolute path or executable name of Tesseract. (minimum characters 1) |
| `TESSDATA_PREFIX` | Empty | Optional directory containing the selected Tesseract language files. Set this for a standalone Windows install if language discovery fails. |

## Flutter build configuration

Use public `--dart-define=API_BASE_URL=...`, `GOOGLE_IOS_CLIENT_ID`, `SENTRY_DSN`, `SENTRY_ERROR_SAMPLE_RATE` or `SENTRY_ENVIRONMENT`. Changing these values requires a Flutter rebuild. Set the iOS reversed client ID URL scheme in the signed target. Keep the existing Flutter development .env asset limited to public application configuration.

The [storage runbook](storage-operations.md) documents the separate machine-local backup and restoration environment.

## Analysis jobs and consent

| Variable | Default | Effect |
| --- | --- | --- |
| `REQUIRE_ANALYSIS_CONSENT` | `true` | Require versioned consent before uploads and new analysis. |
| `ANALYSIS_AGREEMENT_VERSION` | `1` | Current consent version. |
| `ANALYSIS_JOB_EXPIRY_HOURS` | `72` | Time limit for unfinished analysis jobs. |
| `OCR_MAX_SCANNED_PAGES` | `0` | Zero uses the OCR time budget across the entire document. A positive limit produces an explicit error when exceeded. |
| `DOCUMENT_RETENTION_DAYS` | `0` | A positive setting makes inactive records eligible for scheduled cleanup. |

## Connect Flutter to a local backend

For Android USB debugging, start FastAPI on port 8000 and run `adb reverse tcp:8000 tcp:8000`. For an Android emulator without reverse forwarding, run Flutter with `--dart-define=API_BASE_URL=http://10.0.2.2:8000/api`. For a physical phone on your private network, bind FastAPI to the host network interface, permit port 8000 on the private firewall profile and use `http://<computer-LAN-IP>:8000/api` in the debug build. Release builds use the HTTPS Render API unless supplied a build-time override.

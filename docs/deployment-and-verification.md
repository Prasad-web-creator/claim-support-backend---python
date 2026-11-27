# Deployment and verification

FastAPI `/health` is the platform liveness check. Use `/ready` to check MongoDB, the worker and whether the Gemini key is configured. Render Free can suspend idle instances. The app can resume saved jobs after the service wakes. [Render Free service behavior](https://render.com/docs/free) · [Architecture](../../ARCHITECTURE.md) · [Gmail OTP setup](gmail-otp.md)

## Application flow

Flutter reads public operational limits and sign-in availability from /api/app-config. Uploads are checked against the same backend limits before processing. The backend hashes original bytes, optionally optimizes eligible scanned PDF images, and stores one GridFS payload for matching original hashes. Each upload has its own user-scoped identifier and application record.

After versioned consent, a MongoDB worker extracts the prescription and analyzes up to three policies. PyMuPDF reads native PDF text and Tesseract reads scanned pages and images with OpenCV preprocessing. Gemini receives document text for structured extraction and coverage analysis. The service checks source evidence and applies coverage rules.

Retention defaults to keeping policies and prescriptions until deletion. Operators can enable age-based document maintenance. Unfinished jobs expire on a separate schedule. The backend persists the next orphan check and storage measurement in MongoDB. Weekly backups use an independently scheduled encrypted workstation process.

## Staging and release

1. Follow the [service setup guide](service-setup.md) to prepare the organization accounts and the [environment reference](environment.md) to configure staging. Give the test service an isolated MongoDB database and staging-only account credentials. Set a staging `GEMINI_API_KEY` and verify the configured model with synthetic documents.
2. Apply the root GitLab configuration to the canonical repository and retain any organizational includes or runner requirements. The pipeline runs Python lint and tests, a dependency advisory scan, Flutter analysis and tests, and a Docker build with a synthetic OCR smoke test capped at 512 MB and 0.1 CPU. Use a Docker-in-Docker capable trusted runner. Require a successful pipeline before merging the reviewed release branch. Deployment is manual.
3. In Render, use the service settings documented in [service setup](service-setup.md). The root Blueprint is for the monorepo. For a backend-only mirror, point the service to its Dockerfile at the repository root. Disable automatic production deploys, set /health as the check, and deploy the reviewed revision to staging first.
4. Validate real Google Sign-In on signed builds with a returning verified OTP account and a new account. Verify that existing accounts load their dashboard and history. Validate real Gmail HTTPS OTP delivery, expiry, and repeated submissions. Run a synthetic user flow through consent, prescription OCR, manual entry, saved and new policies, interruption and resume, clarification, report retrieval and deletion.
5. Set up the UptimeRobot HTTP monitor and Sentry projects from the [service setup guide](service-setup.md). Confirm staging down/up alerts and verify that Sentry receives only fixed operations and allowed technical metadata. Record staging cold-start latency, peak memory, OCR and policy comparison latency, Atlas usage, and error counts. Render Free has an idle spin-down and resource and usage limits. [Render free service limits](https://render.com/docs/free) · [Render pricing and compute allocation](https://render.com/pricing)
6. Before a live transition, follow the procedures for encrypted backup, restore verification, and legacy TTL migration. Verify indexes and counts before and after. Keep the migration inventory and an encrypted pre-change snapshot through the rollback window. [Storage runbook](storage-operations.md)
7. Publish reviewed insurer rule definitions and run the insurer-approved gold set before relying on automatic coverage decisions. Run the live migration and release through the organization's production release process. If a release must be rolled back, use a build that cannot recreate document TTL indexes, then check activity, storage, and representative reports.

## Earlier verification baseline

| Area | Local result |
|---|---|
| Regression baseline | 369 backend tests and 15 Flutter tests passed before implementation. |
| Regression after implementation | 412 backend tests passed with OCR and real MongoDB enabled. 17 Flutter tests passed. The Android debug APK built successfully. Backend Ruff and the Python runtime dependency advisory scan passed. Flutter analysis completed with two existing warnings and two existing informational findings. |
| Coverage | All 25 synthetic gold-set tests passed. Expected outcomes exercise full, partial, excluded and ambiguous results, eligibility, waiting periods, finance, clarification, and multi-policy behavior. The checked-in runtime catalog contains zero approved insurer versions. |
| OCR and ownership | Real Tesseract and OpenCV read generated images and mixed PDFs. A local API smoke used authenticated upload, MongoDB GridFS, cross-user rejection, extraction and persistence. |
| Authentication | Mocked provider tests verified Google signature, issuer, audience, expiry, and account linking. OTP tests cover hashing, expiry, cooldown, attempt limits, atomic consumption and provider errors. |
| Retention and recovery | Isolated MongoDB 8.0.20 exercised TTL retirement, shared deletion and orphan checks. MongoDB Database Tools and age restored a synthetic 780,000-byte GridFS file byte for byte. Altered encrypted data failed verification. |
| Sentry and privacy | An in-memory Sentry transport received an allowlisted error and trace while excluding a synthetic email, token, raw exception and request breadcrumb. Actual event ingestion remains a staging check. |
| Storage and performance | One 5.21 MB synthetic scan shrank to 0.53 MB in 0.76 s on the Windows test host. A noisier 4.86 MB scan shrank to 1.71 MB in 0.43 s. Three identical uploads used one GridFS payload. The synthetic local OCR smoke took 0.68 s. |

## Durable workflow verification

The refactored backend passed 458 tests, including real local MongoDB, GridFS and Tesseract. A synthetic API test saved consent, checked ownership, uploaded a policy, validated a prescription, restarted FastAPI and retrieved the resulting report with its exact policy quote. The Flutter suite passed 21 tests.

At the default limits, three policies and one prescription represent up to 20 MiB of original upload bytes. For a planning estimate, if that group compresses to 2–4 MB and policies are reused, later analyses add primarily their new prescriptions. Roughly 100–150 users is a scenario for capacity planning. Actual usable capacity depends on the file mix, duplicate rate, BSON, indexes, and report history. Measure active Atlas consumption before forecasting an M10 upgrade.

## Release status

Local verification uses synthetic documents, isolated MongoDB, and mocked Gemini requests. Provider credentials, the organization GitLab pipeline, and signed staging OAuth configuration are required for external checks. Docker was unavailable on the Windows test host. CI can build the image and check synthetic OCR within the Render Free resource limits on a suitable runner. Live Gmail delivery, live Gemini analysis, Render and Atlas behavior, telemetry, production backup scheduling, and real insurer accuracy require staging verification.

See [the file inventory](implementation-files.md) for repository changes.

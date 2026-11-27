# External services and credentials

Use `backend-python/.env` for local development, the Render service's Environment settings for runtime secrets, and GitLab Settings → CI/CD → Variables for credentials used only by CI. Store backup credentials on the backup workstation in an access-controlled file outside this checkout. Enter secret values directly in those locations. The Flutter build may receive public OAuth client IDs and a public API URL.

## MongoDB Atlas

1. Open the organization's Atlas project and identify the existing cluster and application database. Reuse the cluster and database to preserve accounts and history. Atlas lists a 0.5 GB limit for M0. The Database Tools support application database dumps, with M0 restrictions on the admin database and oplog-based backups. Atlas also documents a separately billed, private-preview M0 backup subscription.
2. In Security → Database Access, create or reuse a SCRAM application user with `readWrite` on the application database. Use a separate read-only database user for the scoped backup. Use separately controlled migration access with index-inspection and index-drop permissions on the application database.
3. In Security → Network Access, authorize the Render service's documented outbound ranges and the backup workstation's egress IP. Use the narrowest supported addresses. Copy the Drivers connection URI, URL-encode any credential characters that need escaping, and set `MONGODB_URI` and, if needed, `MONGODB_DATABASE` in Render and the local environment. Keep local tests pointed at an isolated database.
4. Configure Atlas storage alerts where available and track usage against the configured capacity. The DevOps team can choose Edit Configuration on the existing cluster and review the M10 region, pricing, migration window, and backup settings when an upgrade is required.

[Atlas free cluster limits](https://www.mongodb.com/docs/atlas/reference/free-shared-limitations/) · [Atlas backup options](https://www.mongodb.com/docs/atlas/backup-restore-cluster/) · [Render outbound IPs](https://render.com/docs/outbound-ip-addresses)

## Render

1. Give the organization operator access to manage the Render service and authorize the organization GitLab project through Render's Git integration. Reuse the existing service if it points to the correct source and environment, otherwise create a Docker Web Service. Select the organization branch after review, the `backend-python` root for the monorepo, and the existing Dockerfile. Use repository root for a backend-only mirror.
2. Use `/health` as the health check, and turn off automatic deploys for production. Set runtime variables in Environment, including `ENVIRONMENT`, `MONGODB_URI`, the signing secret `JWT_SECRET`, and enabled service settings below. Reuse the existing strong signing secret to preserve active sessions. Generate a strong random secret locally for a new environment. Render supplies the port.
3. Use Free for staging or a resource-fit trial. Render documents 512 MB RAM, 0.1 CPU, idle spin-down after 15 minutes, and 750 free instance hours per workspace per month. Local storage is ephemeral and outbound SMTP ports 25, 465, and 587 are blocked. Verify cold start, OCR memory, and request duration before any production decision.

[Render free services](https://render.com/docs/free) · [Render instance pricing](https://render.com/pricing)

## UptimeRobot

1. Create or reuse an organizational account. The free plan supports five-minute HTTP checks. Give an operator permission to manage monitors and alert contacts.
2. Add an HTTP monitor for the approved HTTPS service URL ending in `/health`. Choose five minutes, add and verify the operations alert email, and check both outage and recovery notifications against staging. The dashboard setup requires no runtime API key or environment variable. For automation, store a scoped monitor-management key in the CI or operations secret store only.
3. Use the monitor for uptime and alerting. The probes can wake an idle service and consume free instance hours. Render may restart or suspend a free service and does not promise continuous availability.

[UptimeRobot free plan](https://help.uptimerobot.com/en/articles/11604710-who-should-use-uptimerobot-s-free-plan) · [Render free services](https://render.com/docs/free)

## Google Sign-In

1. Use the organization's Google Cloud project and configure Google Auth Platform branding, audience, and testing users as needed. An operator with OAuth client administration access can reuse clients that match the app's identity and signing keys. There is no paid API integration required for ID-token verification.
2. Under Clients, create or identify the Web application client ID. Configure it as `GOOGLE_CLIENT_ID` on the backend. It is the expected audience in the returned ID token. Native ID-token verification uses Google's public keys and does not use the Web client secret.
3. Create or verify the Android OAuth clients with the actual package ID and SHA-1 fingerprints for development and the production Play App Signing certificate. For iOS, register the bundle identifier and configure the client ID and reversed-client-ID URL scheme. Use test accounts when the consent screen is in Testing.
4. Validate sign-in and account linking against staging with each supported signing configuration and an existing email OTP account.

[Backend ID-token verification](https://developers.google.com/identity/sign-in/android/backend-auth) · [Android signing fingerprints](https://developers.google.com/android/guides/client-auth)

## Email OTP

For Microsoft 365 SMTP OAuth, set `EMAIL_OTP_PROVIDER=smtp_oauth`, `SMTP_HOST=smtp.office365.com`, `SMTP_PORT=587` and provide `SMTP_TENANT_ID`, `SMTP_CLIENT_ID`, `SMTP_CLIENT_SECRET`, `SMTP_USERNAME` and `SMTP_FROM_EMAIL` in the backend environment. Use the mailbox authorized for the Exchange Online service principal. Run `python -m scripts.email_check` from the backend directory to verify token acquisition and SMTP authentication without sending a message. Render Free blocks outbound SMTP on port 587. [Microsoft SMTP OAuth](https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth) · [Render networking restrictions](https://render.com/docs/free#other-limitations)

For Gmail delivery over HTTPS, see the [Gmail OTP setup](gmail-otp.md#configure-gmail-otp).

1. Create or reuse the organization's Resend account. Add a sending subdomain under Domains and apply its SPF and DKIM records with the DNS administrator. Verify DNS in Resend and choose a sending address on the verified domain. The free tier on the published plan allows 100 sends per day, subject to the plan's other quotas.
2. Under API Keys, create or reuse a sending-only API key scoped to the verified domain. In Render set `EMAIL_OTP_PROVIDER=resend`, `EMAIL_API_KEY`, and `EMAIL_FROM_ADDRESS`. Use the same variables in an isolated test environment with a separate key where possible. HTTPS works within Render Free's SMTP restrictions. Existing SMTP credentials can still be used on permitted networks with `EMAIL_OTP_PROVIDER=smtp` and the `SMTP_*` settings.
3. Set a long random `OTP_HASH_SECRET` in Render or use the existing JWT signing secret as the fallback. Configure the OTP length, expiry, cooldown, attempt limits, and rate limits through the backend environment. Test deliverability and inspect logs for accidental disclosure.

[Resend verified domains](https://resend.com/docs/dashboard/domains/introduction) · [Resend pricing](https://resend.com/pricing) · [Render SMTP restrictions](https://render.com/docs/free)

## Gemini

1. Create a Gemini API key in the Google AI Studio project approved for processing prescription and policy text.
2. Set `GEMINI_API_KEY` as a secret on the Render backend or in the local backend `.env`. Use `AI_MODEL` to override the Gemini model. Verify the integration in staging with synthetic inputs.

[Gemini developer setup](https://ai.google.dev/gemini-api/docs/get-started) · [Gemini terms](https://ai.google.dev/gemini-api/terms)

## Sentry

1. Create or reuse organization projects for Python and, if enabled, Flutter. Project administration is required for the initial configuration. The free Developer tier includes one user and an event quota, so it is suitable for a trial with the designated operator.
2. Copy each project's DSN from Client Keys. Put the backend DSN in Render as `SENTRY_DSN`, enable `SENTRY_ENABLED`, and set the trace and error sampling variables. Use a separate public DSN for Flutter. Reuse an existing project DSN only if it routes to the appropriate project and environment.
3. Enable server-side data scrubbing and restrict access to the projects. Trigger synthetic failures in staging and verify the event payload contains no user data, document content, tokens, or OTPs. No admin token is needed for runtime event ingestion.

[Sentry plans](https://sentry.io/pricing/) · [Sentry data scrubbing](https://docs.sentry.io/security-legal-pii/scrubbing/)

## Organization GitLab

1. Identify the canonical repository, its default branch, and the runner pool. A Developer can push a work branch and open a merge request. A Maintainer or Owner can manage runners, protected branches, and CI settings. Retain existing workflow/includes when merging the CI configuration. Free-tier CI can use the project's available hosted minutes or an organization runner.
2. Protect the release branch and require a passing pipeline and the organization's review before merging. Check that Linux runners can run the Python, Flutter, and Docker jobs. Use a trusted Docker-capable runner for image validation.
3. Tests use isolated fixtures. For any organization release integration added later, store credentials as masked and protected variables scoped to the correct environment. Prefer the existing authorized Render integration for manual deployments and keep production secrets out of merge-request jobs.

[GitLab CI variables](https://docs.gitlab.com/ci/variables/) · [GitLab protected branches](https://docs.gitlab.com/user/project/repository/branches/protected/)

## Backup storage

1. Install MongoDB Database Tools on the backup workstation. Use a local encrypted volume, for example an approved BitLocker data volume. Grant the scheduler identity access to the encrypted destination and the separate read-only Atlas credentials. Register the recovery key in the organization's key escrow.
2. For an approved cloud destination such as organization-managed Google Drive, upload only locally encrypted backup artifacts. Grant the transfer identity write access only to the approved folder. Review storage quota, retention, and organizational policy before use. An existing approved encrypted location may be reused. A local encrypted volume requires no cloud account or application environment variable.
3. Configure the schedule, destination and retention on the workstation and verify restoration into an isolated database with its own credentials.

[MongoDB Database Tools](https://www.mongodb.com/docs/database-tools/)

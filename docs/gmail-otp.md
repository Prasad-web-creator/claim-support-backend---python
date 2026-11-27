# Gmail OTP

## Configure Gmail OTP

1. Open the Google Cloud Console and select the organization project for transactional email. Go to **APIs & Services → Library**, find **Gmail API**, and enable it.
2. Open **Google Auth Platform**. Under **Branding**, provide the application's support details. Under **Audience**, choose the audience that includes the sender mailbox. For an External app in Testing, add the dedicated mailbox as a test user. Under **Data Access**, add the send-only scope `https://www.googleapis.com/auth/gmail.send`. [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
3. Under **Clients**, create a **Web application** OAuth client for email delivery. If you use the OAuth Playground for manual setup, add the exact redirect URI `https://developers.google.com/oauthplayground` to this client. Keep this OAuth client separate from the client configured for Google Sign-In. [Google OAuth for web server applications](https://developers.google.com/identity/protocols/oauth2/web-server)
4. Open the OAuth Playground. Select the settings gear, choose **Use your own OAuth credentials**, and enter the new client ID and secret. Authorize the Gmail send scope while signed in as the dedicated delivery mailbox. Exchange the authorization code for tokens and copy the refresh token directly into the backend secret store. [Google OAuth Playground](https://developers.google.com/oauthplayground/)
5. In the Render service, open **Environment**, or use the ignored local `backend-python/.env` for local testing. Set these values and restart the backend.

   | Variable | Value |
   | --- | --- |
   | `EMAIL_OTP_PROVIDER` | `gmail` |
   | `GMAIL_CLIENT_ID` | The email-delivery OAuth client ID |
   | `GMAIL_CLIENT_SECRET` | The email-delivery OAuth client secret |
   | `GMAIL_REFRESH_TOKEN` | The refresh token issued for the sender mailbox |
   | `GMAIL_FROM_ADDRESS` | The authorized sender mailbox address |

6. In staging, request an email verification code using a test inbox you control and verify it in the app. Check that wrong or reused codes fail. Check redacted server logs for provider errors if delivery fails. The backend uses HTTPS through the Gmail send endpoint, which works with Render Free's outbound SMTP restrictions. SMTP remains available through `EMAIL_OTP_PROVIDER=smtp` on networks that permit it. [Gmail send method](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send) · [Render Free networking](https://render.com/docs/free)

Google generally expires External Testing refresh tokens after seven days for this scope. Use an approved Internal OAuth app where the Workspace organization supports it, or follow Google's requirements to publish an External app. Gmail and the mailbox have sending limits. Google can also revoke previously issued refresh tokens when sender access or policies change. [Google refresh token expiration](https://developers.google.com/identity/protocols/oauth2#expiration) · [Google Workspace Gmail limits](https://support.google.com/a/answer/166852)

The backend keeps Gmail access tokens in process memory. It stores a verifier for each OTP and sends only the verification email through Gmail. It sends no policy or prescription content through the OTP service.

New session refresh tokens are stored as hashes. A valid refresh token from a prior deployment rotates to hashed storage when used. The signed access token and session refresh API response remain compatible with existing clients.

# Setup

This takes about 30 minutes, and you only do it once. In the examples, `YOUR_TENANT` is your Auth0 tenant domain, such as `example.us.auth0.com`.

Dashboard labels can change. If one doesn't match, search the page for the key word.

## 1. Auth0 application

1. Go to **Applications → Applications → Create Application** and choose **Regular Web Application**.
2. Set these URLs:

   | Field | Value |
   |---|---|
   | Allowed Callback URLs | `http://localhost:5000/callback` |
   | Allowed Logout URLs | `http://localhost:5000, http://localhost:5000/` |
   | Allowed Web Origins | `http://localhost:5000` |

3. Under **Advanced Settings → Grant Types**, make sure **Authorization Code**, **Refresh Token** and **Token Vault** are on.
4. Turn off **Allow Refresh Token Rotation**. Token Vault doesn't support rotating tokens.

## 2. Auth0 API

1. Go to **Applications → APIs → Create API**, and use the identifier `https://agent-passport`.
2. On **Permissions**, add `github:read` and `gmail:read`.
3. In **Settings**, turn on **Allow Offline Access** and leave RBAC off.
4. On your application's **API Access** tab, click **Edit** on this API. Under **User-delegated Access**, grant both permissions.

## 3. My Account API

Your app uses this API to connect accounts on your behalf.

1. On your application's **API Access** tab, click **Edit** on **Auth0 My Account**. Under **User-delegated Access**, grant `create:me:connected_accounts`, `read:me:connected_accounts` and `delete:me:connected_accounts`.
2. In your application's settings, under **Multi-Resource Refresh Token**, turn on **Auth0 My Account API**.
3. In **Applications → APIs → Auth0 My Account API → Settings**, turn on **Allow Skipping User Consent**.
4. In the same settings, turn off **Require 2FA** under **Default Policy**, for local use only. The app makes this call in the background, where nobody can answer a 2FA prompt. In production, keep it on and add a step-up prompt.
5. In **Security → Multi-factor Auth**, don't set the policy to **Always**. It blocks getting tokens from Token Vault.

## 4. GitHub

1. [Create a GitHub App](https://github.com/settings/apps/new):
   - **Homepage URL:** `https://YOUR_TENANT`
   - **Callback URL:** `https://YOUR_TENANT/login/callback`
   - **Webhook:** untick **Active**
   - **Repository permissions:** **Metadata** read-only
2. Copy the **Client ID**, and generate a **Client Secret**. Both go into Auth0, not `.env`.
3. Click **Install App** and install it on your account.
4. In Auth0, go to **Authentication → Social → Create Connection → GitHub**:
   - Name it exactly `github`.
   - Paste in the Client ID and Client Secret.
   - Set **Purpose** to **Connected Accounts for Token Vault**.
   - Under **Permissions**, turn on **Offline Access**.
5. On the connection's **Applications** tab, turn on your application.

**Try Connection** will fail for this connection, and that's expected, because it's set up for Token Vault and not for login. Test it with **Connect GitHub** on the panel instead.

## 5. Gmail (optional)

1. In Google Cloud, enable the **Gmail API**.
2. In the **Google Auth Platform**, set the audience to **External** and add your address as a **Test user**.
3. Create a **Web application** client, with the redirect URI `https://YOUR_TENANT/login/callback`.
4. In Auth0, go to **Authentication → Social → Create Connection → Google**:
   - Name it exactly `gmail`. If you already use `google-oauth2` for login, leave that one alone.
   - Set **Purpose** to **Connected Accounts for Token Vault**.
   - Under **Permissions**, turn on **Offline Access** and add `https://www.googleapis.com/auth/gmail.readonly`.
5. On the connection's **Applications** tab, turn on your application.

Google shows an "unverified app" warning for test apps. `gmail.readonly` is a restricted scope, so opening this to the public requires Google's verification and a security assessment.

## 6. Run it

```bash
cp .env.example .env   # then fill in your values
python run.py
```

Open `http://localhost:5000`, not `127.0.0.1`, because only `localhost` is in your Auth0 URLs.

`.env` changes need a restart. After changing an Auth0 setting that affects tokens, log out and back in, because your current session keeps its old tokens.

## Troubleshooting

The panel shows the exact error, and these are the ones you're most likely to see.

| Error | Cause | Fix |
|---|---|---|
| `Callback URL mismatch` | The URL isn't listed, or `.env` has another app's Client ID | Check the URLs on the app that matches `AUTH0_CLIENT_ID` |
| `Service not found` | `AUTH0_AUDIENCE` doesn't match an API identifier | Copy the identifier exactly |
| `Client ... is not authorized to access resource server` | The app has no grant for that API | Grant it under **User-delegated Access**, on the right application |
| The token has no API scopes | The permission isn't defined or granted, or RBAC is on | Check the API's **Permissions** and the app's grant |
| No `offline_access` in the scope line | **Allow Offline Access** is off, or the login is old | Turn it on, then log out and back in |
| `Rotating refresh token not supported` | Rotation is on, or the login predates turning it off | Turn it off, then log out and back in |
| `User does not meet the required authentication assurance level` | The My Account **Require 2FA** policy, or MFA set to **Always** | See step 3 |
| `connection was not found or not enabled for client` | The connection is missing, misnamed, or not enabled for your app | Check its name and **Applications** tab |
| `The connection is not active for authentication` | You clicked **Try Connection** | Expected. Use **Connect** on the panel |
| `The code passed is incorrect or expired` | The page was reloaded, or an extension reloaded it, so GitHub's one-time code was reused | Retry once in a private window |
| Old values keep being used | VS Code's `python.terminal.useEnvFile` loaded an old `.env` into the terminal | The app already overrides this, so restart it |

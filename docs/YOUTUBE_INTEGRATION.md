# YouTube Data API integration

MATRIX uses the official YouTube Data API v3 and Google OAuth 2.0. It does not scrape YouTube pages or store a Google password.

## Supported actions

| Tool | Effect | MATRIX policy |
|---|---|---|
| `youtube.get_channel` | Read the authenticated channel and upload playlist | Read-only, `youtube.read` |
| `youtube.list_videos` | Read recent uploads and basic statistics | Read-only, `youtube.read` |
| `youtube.list_comments` | Read comments on a supplied video ID | Read-only, `youtube.read` |
| `youtube.reply_to_comment` | Publish a public reply | HIGH risk, explicit owner approval and `youtube.write` |
| `youtube.upload_video` | Upload a local video; defaults to private | HIGH risk, explicit owner approval and `youtube.write` |

There is intentionally no auto-delete tool. A YouTube upload is never silently made public: the default privacy status is `private`, and every upload is approval-gated regardless of the selected privacy setting.

## Google OAuth setup

1. Create or select a project in Google Cloud Console.
2. Enable **YouTube Data API v3**.
3. Configure the OAuth consent screen and create an OAuth client for a desktop application.
4. Obtain a refresh token for the Google account that owns or manages the target YouTube channel.
5. For reads, grant `https://www.googleapis.com/auth/youtube.readonly`.
6. For comment replies, also grant `https://www.googleapis.com/auth/youtube.force-ssl`.
7. For uploads, also grant `https://www.googleapis.com/auth/youtube.upload`.
8. Store credentials only in the MATRIX process environment or a protected local secret store:

```dotenv
MATRIX_GOOGLE_CLIENT_ID=...
MATRIX_GOOGLE_CLIENT_SECRET=...
MATRIX_GOOGLE_REFRESH_TOKEN=...
```

Do not commit these values or paste them into chat. MATRIX exchanges the refresh token for short-lived access tokens, caches them in process memory, and never includes tokens in tool results or logs.

## Enable writes explicitly

Read tools are available under the default `youtube.read` scope. Public replies and uploads additionally require an owner opt-in:

```dotenv
MATRIX_EXTRA_SCOPES=youtube.write
```

This does not bypass MATRIX approvals. HIGH-risk actions still pause until the owner approves the exact action and arguments. Do not enable this scope if the owner only needs channel analytics.

## Upload limits and safety

- Upload source files must be inside MATRIX's approved filesystem roots.
- Maximum file size is 512 MiB.
- Uploads use YouTube's resumable upload endpoint.
- Privacy defaults to `private`; public/unlisted publishing is still HIGH risk.
- API errors are summarized without exposing OAuth response bodies or tokens.
- YouTube quota and Google OAuth consent limits apply; this is not unlimited or guaranteed-free automation.

## Acceptance still required

A code/CI pass does not prove that a Google account is connected. Before production use, the owner must configure OAuth, verify the intended channel identity, test read-only calls, approve a private test upload, confirm processing and visibility in YouTube Studio, and check the project's live API quota. MATRIX does not claim that this has been completed without those live account checks.

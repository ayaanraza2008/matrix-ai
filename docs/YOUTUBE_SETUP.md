# YouTube channel integration

MATRIX uses the official YouTube Data API v3 and a user-owned Google OAuth Desktop client. It does not use browser scraping or store a Google password.

## One-time owner setup

1. In Google Cloud Console, create/select a project and enable **YouTube Data API v3**.
2. Configure the OAuth consent screen and create an OAuth client of type **Desktop app**.
3. Download the OAuth client JSON to a private local path.
4. Set `MATRIX_YOUTUBE_CLIENT_SECRETS` to that path.
5. Install the optional API dependencies: `pip install -e ".[youtube]"`.
6. Run `matrix youtube-auth` on the PC that will run MATRIX and complete Google's consent screen.
7. MATRIX stores the refresh token under `MATRIX_HOME/youtube-token.json` by default. Keep this file private and never commit or send it.

The consent flow requests YouTube read, comment-management and upload scopes. The application permission system is narrower: `youtube.read` is enabled by default; `youtube.write` and `youtube.upload` must be explicitly added to `MATRIX_EXTRA_SCOPES`. Example:

```dotenv
MATRIX_EXTRA_SCOPES=youtube.write;youtube.upload
```

The token is also checked for the actual OAuth scope granted by Google. An application-level scope alone cannot manufacture Google permissions.

## Available tools

| Tool | Purpose | MATRIX permission | Approval |
|---|---|---|---|
| `youtube.get_channel` | Read connected channel identity/statistics | `youtube.read` | No |
| `youtube.list_videos` | List recent channel uploads | `youtube.read` | No |
| `youtube.list_comments` | Read video comment threads | `youtube.read` | No |
| `youtube.reply_comment` | Reply to a comment | `youtube.write` | Exact owner approval |
| `youtube.upload_video` | Upload a file from an approved MATRIX file root | `youtube.upload` | Critical-action approval |

Uploads default to **private**. Public or unlisted visibility must be specified in the approved action. The source file must be inside an approved filesystem root and within `MATRIX_YOUTUBE_MAX_UPLOAD_BYTES` (default 2 GB).

## Free-tier and limitations

- The API has a daily quota. YouTube upload operations consume quota and Google may require project verification or compliance checks; MATRIX cannot promise unlimited free uploads.
- The Google API may reject uploads for quota, channel, account or policy reasons. MATRIX reports provider failure rather than claiming success.
- Public comments, uploads and other writes remain approval-gated. No automatic reply or public publishing is enabled without an explicit owner-approved task.
- OAuth setup requires the owner to create the Google Cloud OAuth client and complete the consent screen. MATRIX cannot authorize itself into a personal Google account.
- This connector does not yet provide livestream management, analytics reports, thumbnails, playlists, Shorts-specific publishing or scheduled publishing.

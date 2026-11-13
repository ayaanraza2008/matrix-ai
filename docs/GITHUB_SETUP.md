# GitHub integration

MATRIX uses the official GitHub REST API. Set `MATRIX_GITHUB_TOKEN` in the local process environment; use a fine-grained personal access token scoped only to the repositories and permissions required. Never paste the token into chat, source control or screenshots.

## Available actions

| Tool | Permission | Risk / approval |
|---|---|---|
| `github_whoami` | `github.read` | Low; no approval |
| `github_get_repository` | `github.read` | Low; no approval |
| `github_list_issues` | `github.read` | Low; bounded to 30 results |
| `github_get_pull_request` | `github.read` | Low; no approval |
| `github_create_issue` | `github.write` | High; exact owner approval |
| `github_comment_issue` | `github.write` | High; exact owner approval |

Read-only access is enabled by default. Write access is not. To opt in, set:

```dotenv
MATRIX_EXTRA_SCOPES=github.write
```

MATRIX still requests exact approval for every issue creation or comment. A configured scope does not override provider-side token permissions.

## Setup and limits

1. Create a fine-grained GitHub token with read-only repository metadata/issues/pull requests access for the repositories MATRIX needs.
2. Set `MATRIX_GITHUB_TOKEN` in the local environment and restart MATRIX.
3. Check `matrix doctor` and run a read-only identity/repository request.
4. Only if issue writes are needed, explicitly opt in to `github.write` and approve each proposed write in MATRIX.

The connector never returns or logs the token. Requests use bounded timeouts, minimal response fields, and bounded pagination. A live smoke test still requires the owner's token and repository permissions. GitHub API quotas and fine-grained token restrictions apply.

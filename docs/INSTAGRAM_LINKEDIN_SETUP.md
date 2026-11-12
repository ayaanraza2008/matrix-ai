# Instagram and LinkedIn integrations

MATRIX uses the official Meta Graph API and LinkedIn REST API. These connectors require owner-created developer applications and access tokens. They do not scrape websites or request account passwords.

## Instagram (Business or Creator only)

1. Convert the Instagram account to a Business or Creator account and link it to a Facebook Page.
2. Create a Meta developer app and add the Instagram Graph API / required Facebook Login permissions.
3. Obtain a token for the linked Page/Instagram account with the permissions required by the actions you intend to use.
4. Set `MATRIX_INSTAGRAM_ACCESS_TOKEN`, `MATRIX_INSTAGRAM_USER_ID`, and optionally `MATRIX_META_GRAPH_VERSION`.
5. Run MATRIX's integration health check and verify the returned username before enabling write scopes.

Read operations use `instagram.read`. Comment replies and image publishing require the owner to opt in with `MATRIX_EXTRA_SCOPES=instagram.write`. Every reply requires approval; publishing is CRITICAL and requires exact approval. Image publishing requires a public HTTPS image URL because Meta fetches the image from its servers; local file paths are not uploaded by this connector.

Personal/consumer Instagram accounts are not supported by these official Graph API tools. Meta app review, business verification, permissions and rate limits may apply.

## LinkedIn

1. Create a LinkedIn developer application and request the applicable product access (for example, Community Management API) and permissions.
2. Obtain an OAuth access token with the required member or organization permissions.
3. Set `MATRIX_LINKEDIN_ACCESS_TOKEN` and `MATRIX_LINKEDIN_API_VERSION`.
4. Optionally set `MATRIX_LINKEDIN_PERSON_URN` or `MATRIX_LINKEDIN_ORGANIZATION_URN`. If neither is set, MATRIX derives the member URN from the authenticated `/rest/me` response. Organization publishing requires the organization URN and the token must be authorized to act for that organization.

Read operations use `linkedin.read`. Comments and post publishing require `MATRIX_EXTRA_SCOPES=linkedin.write`; comments are HIGH risk and posts are CRITICAL, both requiring exact owner approval. LinkedIn API product access is not automatically granted to every developer app.

## Available actions

| Platform | Read | Write |
|---|---|---|
| Instagram | Profile, recent media, comments | Reply to comments, publish image posts |
| LinkedIn | Member profile, posts, comments | Comment on posts, publish text posts |

Read content is marked untrusted before it is passed back into the planner. The assistant cannot use content from comments or posts as instructions to bypass policy.

## Token safety and costs

Store tokens only in the MATRIX process environment or a private local secret store. Never paste them into chat, source control or issue comments. Rotate any token that has been exposed. Provider API access may require app review and may be rate-limited; MATRIX cannot promise that every account qualifies for these APIs or that publishing is unlimited/free.

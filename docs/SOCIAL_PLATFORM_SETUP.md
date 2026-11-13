# Social platform connectors

MATRIX includes narrow official-API actions for LinkedIn, Instagram Professional accounts and WhatsApp Business Cloud API. These are not browser automation and do not bypass platform approval, app review, OAuth scope or account eligibility requirements.

## LinkedIn

Configure:
- `MATRIX_LINKEDIN_ACCESS_TOKEN`
- `MATRIX_LINKEDIN_AUTHOR_URN` (for example `urn:li:person:123...` or an organization URN)
- `MATRIX_LINKEDIN_API_VERSION` (defaults to `202609`; update it to a currently supported YYYYMM version)

Tool: `linkedin_create_post`. It creates a text-only organic post using the official Posts API. It requires `MATRIX_EXTRA_SCOPES=linkedin.write` and exact owner approval for each post. The LinkedIn app must be approved for `w_member_social` or `w_organization_social`, and the member must have the required organization role when posting as an organization.

Official API: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api

## Instagram Professional

Configure:
- `MATRIX_INSTAGRAM_ACCESS_TOKEN`
- `MATRIX_META_GRAPH_VERSION` (set a currently supported Graph API version from your Meta app configuration)

Tools:
- `instagram_list_comments` — bounded read of comments for a media ID; requires `instagram.read`.
- `instagram_reply_comment` — comment reply; requires `instagram.write` and exact owner approval.

Instagram Graph API access requires a Professional (Business or Creator) account and a linked Facebook Page for the Facebook Login flow. Consumer/personal accounts are not supported by this connector. The Meta app must have the required permissions (such as `instagram_manage_comments`) and access level.

Official guide: https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api

## WhatsApp Business Cloud API

Configure:
- `MATRIX_WHATSAPP_ACCESS_TOKEN`
- `MATRIX_WHATSAPP_PHONE_NUMBER_ID`
- `MATRIX_META_GRAPH_VERSION`

Tool: `whatsapp_send_text`. It sends one text message to an E.164 recipient and requires `MATRIX_EXTRA_SCOPES=whatsapp.write` plus exact owner approval. Recipients must have opted in. Free-form messages are subject to WhatsApp's customer-service window and policy; approved templates may be required outside it. Meta's business messaging pricing and account limits apply.

Official guide: https://developers.facebook.com/documentation/business-messaging/whatsapp/overview

## Safety and scope boundaries

- All public/social sends and replies are high-risk, non-idempotent actions; MATRIX requires per-action approval and never blindly retries them.
- Provider responses must return a post/reply/message ID before MATRIX records success.
- Tokens are read from local environment configuration and never returned in tool outputs or audit logs.
- This PR does not implement Instagram media publishing, LinkedIn image/video uploads, WhatsApp inbound webhooks, WhatsApp template management, DMs on Instagram, or phone/PSTN calls.
- WhatsApp inbound messages require a publicly reachable HTTPS webhook with Meta verification/signature checks. A private Tailscale-only listener is not reachable by Meta; do not expose it without a deliberate deployment and security review.
- Live acceptance requires owner-supplied app credentials, approved permissions and test accounts. API quotas, business eligibility and provider charges may apply.

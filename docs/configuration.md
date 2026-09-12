# Configuration

Settings use the `APP_` prefix and load `.env` from the working directory. Unknown settings are rejected. Required local fields are:

- `APP_DATABASE_PATH`
- `APP_DATA_DIR`
- `APP_BOOTSTRAP_USER_EXTERNAL_KEY`
- `APP_BOOTSTRAP_USER_DISPLAY_NAME`
- `APP_BOOTSTRAP_MAIL_PROVIDER`
- `APP_BOOTSTRAP_MAIL_ACCOUNT_KEY`
- `APP_BOOTSTRAP_MAIL_DISPLAY_NAME`
- `APP_QQ_USER_OPENID`

Optional settings include `APP_HOST`, `APP_PORT`, `APP_LOG_LEVEL`, `APP_QQ_ENABLED`,
`APP_QQ_APP_ID`, `APP_QQ_APP_SECRET`, `APP_QQ_API_BASE_URL`,
`APP_QQ_TOKEN_BASE_URL`, `APP_QQ_WEBHOOK_PATH`, `APP_QQ_WEBHOOK_TOKEN`,
`APP_QQ_ACCESS_TOKEN`, `APP_QQ_REQUEST_TIMEOUT_SECONDS`,
`APP_QQ_DISPATCH_TIMESTAMP_MAX_AGE_SECONDS`,
`APP_QQ_DOWNLOAD_TIMEOUT_SECONDS`, `APP_QQ_DOWNLOAD_DEADLINE_SECONDS`,
`APP_QQ_MAX_DOWNLOAD_BYTES`, `APP_LLM_API_KEY`, `APP_IMAP_PASSWORD`,
`APP_EMBEDDING_API_KEY`, and `APP_CHROMA_PATH`.

Safety defaults are `APP_AGENT_MAX_TOOL_CALLS=8`, `APP_AGENT_MAX_RUN_SECONDS=120`, `APP_AGENT_TOOL_TIMEOUT_SECONDS=10`, `APP_AGENT_MAX_RESULT_CHARS=12000`, and `APP_MAX_UPLOAD_BYTES=10485760`.

`APP_QQ_DISPATCH_TIMESTAMP_MAX_AGE_SECONDS` defaults to `300` and accepts values from `1` through `3600` seconds.

`APP_QQ_ENABLED` defaults to `false`. Complete QQ credentials and an explicit
HTTPS token URL do not construct or start production botpy while this gate is
false. When enabled, both QQ API and token base URLs must use HTTPS. The
Starlette listener binds through `APP_HOST` and `APP_PORT`; the QQ route uses
`APP_QQ_WEBHOOK_PATH`.

Use `chmod 600 .env` and `chmod 700 ./data ./backups`. Credentials are opaque optional settings, never logged or persisted as plaintext identity fields. A non-empty `APP_EMBEDDING_API_KEY` requires `APP_CHROMA_PATH` for production indexing and rebuilds. An empty or missing key disables production indexing even when the default Chroma path remains configured. `APP_EMBEDDING_API_KEY` is sent only as a Bearer credential to Alibaba Cloud Bailian.

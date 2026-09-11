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

Optional settings include `APP_HOST`, `APP_PORT`, `APP_LOG_LEVEL`, `APP_QQ_WEBHOOK_TOKEN`, `APP_QQ_ACCESS_TOKEN`, `APP_LLM_API_KEY`, `APP_IMAP_PASSWORD`, `APP_EMBEDDING_API_KEY`, and `APP_CHROMA_PATH`.

Safety defaults are `APP_AGENT_MAX_TOOL_CALLS=8`, `APP_AGENT_MAX_RUN_SECONDS=120`, `APP_AGENT_TOOL_TIMEOUT_SECONDS=10`, `APP_AGENT_MAX_RESULT_CHARS=12000`, and `APP_MAX_UPLOAD_BYTES=10485760`.

Use `chmod 600 .env` and `chmod 700 ./data ./backups`. Credentials are opaque optional settings, never logged or persisted as plaintext identity fields. A non-empty `APP_EMBEDDING_API_KEY` requires `APP_CHROMA_PATH` for production indexing and rebuilds. An empty or missing key disables production indexing even when the default Chroma path remains configured. `APP_EMBEDDING_API_KEY` is sent only as a Bearer credential to Alibaba Cloud Bailian.

# Codex Instructions for Stock Key10

## Mandatory context protocol

Before analyzing, changing, or reporting on this project:

1. Read `PROJECT_STATE.md` completely.
2. For any claim about current data, fetch `latest/manifest.json` and the relevant authoritative archive.
3. For any claim about server health, inspect the real server service, timer, and logs during the current task. If the environment cannot access the server, say “未验证”; never infer health from old chat history.
4. For any claim about ChatGPT scheduled tasks, read the current automation status and recent-run metadata.
5. Keep these categories separate in reasoning and user-facing reports:
   - confirmed rules;
   - live verified state;
   - dated historical facts;
   - unresolved conflicts or unverified assumptions.
6. Never silently resolve conflicts between `PROJECT_STATE.md`, live configuration, and user instructions. The user's latest explicit instruction wins for rules; live system inspection wins for operational status.
7. Treat conversation memories and summaries only as retrieval hints. They are not an authority for current configuration or runtime health.
8. After a user-confirmed rule change or a material infrastructure change, update `PROJECT_STATE.md` in the same workstream and append its change log.
9. Never report a task as completed merely because it was created, enabled, or triggered. Verify the expected artifact or visible receipt.
10. Never store credentials or secrets in project documentation.

## Delivery discipline

- Use exact dates and `Asia/Shanghai` when discussing trading-day schedules.
- Do not use stale trade dates as current results.
- Do not fall back to an older model or bundle unless the user explicitly authorizes it.
- If required live access is unavailable, state the boundary and stop short of claiming success.

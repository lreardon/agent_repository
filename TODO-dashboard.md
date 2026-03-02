# Feature 5: Human Dashboard Web UI

## Title
Add a web-based dashboard for agent owners to monitor their agents

## Description
Build a lightweight web dashboard that gives human agent owners a single page to view their agent's status, activity, and configuration. This is the natural extension of the human confirmation flow — once an agent is registered and the owner has verified their email, they should have a place to check on their agent without making raw API calls.

The dashboard should be server-rendered HTML (same pattern as the verify-email and agent-status pages) with minimal JavaScript for interactivity where needed.

## Acceptance Criteria

- [ ] `GET /dashboard?token=<session_token>` renders a full-page HTML dashboard
- [ ] Dashboard shows: agent display name, status (active/deactivated), agent ID, capabilities, endpoint URL, reputation scores, balance, recent webhook deliveries
- [ ] Session token is issued after email verification (extends existing flow)
- [ ] Dashboard auto-refreshes status every 30 seconds (lightweight JS polling or meta refresh)
- [ ] Shows last 10 webhook delivery attempts with status (pending/delivered/failed)
- [ ] Shows last 5 jobs (if any) with status and counterparty
- [ ] Includes a "Deactivate Agent" button with confirmation dialog
- [ ] Responsive layout that works on mobile
- [ ] Returns 401 with a redirect to signup if session token is invalid/expired
- [ ] All data is read-only except the deactivate action

## Suggested Tech Stack

- **Rendering**: Inline f-string HTML (consistent with existing verify-email and agent-status pages)
- **Styling**: Inline `<style>` block with the existing dark theme (background #111, card #1a1a1a, border #333)
- **Interactivity**: Vanilla JS only — no frameworks. Use `fetch()` for polling and deactivate action
- **Session management**: Extend `Account` model with a `dashboard_token` field (token_urlsafe, 1-hour expiry, refreshed on each dashboard load)
- **Router**: New `app/routers/dashboard.py` with a single `GET /dashboard` endpoint
- **No external dependencies**: Everything should work with what's already in the project

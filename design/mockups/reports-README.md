# Reports — mockup and planning notes

`reports-mockup.html` is a standalone, dummy-data prototype of the Reports
page. Open it directly in a browser to click through it. No backend, no
real data — for UX iteration only.

## Report types

- **Activity reports** — events over a custom date range: people added,
  gifts given, emails sent, flows triggered. Each is shown as a total plus
  a ranked breakdown, and clicking a ranked row drills into the underlying
  contact list (paginated, still scoped to the selected date range).
- **Snapshot reports** — state at a point in time: total user count, users
  by badge. Badges (e.g. VIP, Family friend) are non-mutually-exclusive
  tags, so badge counts are shown as a bar list, not a pie chart.

## Access control (important)

- **Agents** can only see their own activity and their own contacts —
  never another agent's numbers or contacts.
- **Admins** can see agency-wide totals, plus a per-agent ranked
  breakdown they can drill into to view any individual agent's report
  (using the same view an agent sees for themselves).
- This needs to be enforced at the API/query layer based on the
  requesting user's role, not just hidden in the UI — the agent-scoped
  endpoint should never accept or return another agent's data.

## Still open

- Whether "admin" is a role/flag on the existing `User` model or a
  separate auth path.
- Exact drill-down UX for a single ranked item beyond "list of
  contacts" (e.g. filter/breakdown by badge instead).
- Data source for each event type — whether ActionLog / ContactAuditLog
  already cover all four activity types or a unified events table is
  needed.

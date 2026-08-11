# hermes-cockpit

Hermes cockpit is a lightweight control surface for Hermes workflows.

## What it is

- A static HTML/CSS/JS MVP shell
- No build step
- Live status data from the local Hermes environment and kanban database

## Local run

From the repository root:

```bash
python3 scripts/cockpit_server.py --port 8000
```

Then open http://localhost:8000 in your browser.

The server also exposes a JSON endpoint at `http://localhost:8000/api/status` that the cockpit uses to populate the live panel.

## Remote Hermes Agent target

If you want the cockpit to read status from a Hermes Agent running in Docker on another host, set both the agent endpoint and the kanban database path before starting the server:

```bash
HERMES_AGENT_BASE_URL=http://UNRAID_HOST:9119 \
HERMES_KANBAN_DB=/path/to/shared/kanban.db \
python3 scripts/cockpit_server.py --port 8000
```

That keeps the cockpit pointed at the remote Hermes Agent for live status while still reading the shared kanban database from disk.

## Verify

1. Start the cockpit server.
2. Open the cockpit in a browser.
3. Confirm the top-right status reads as connected and the live cards show real values.
4. Optional smoke check from the terminal:

```bash
curl -s http://localhost:8000/api/status | python3 -m json.tool
```

## Project layout

- `src/index.html` — cockpit shell markup
- `src/styles.css` — layout and visual styling
- `src/main.js` — live data fetch and panel rendering
- `scripts/cockpit_server.py` — tiny local server with the live status endpoint
- `docs/roadmap.md` — near-term product direction

## Status

The cockpit now renders live Hermes status data and can be refreshed manually from the UI.

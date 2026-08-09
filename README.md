# hermes-cockpit

Hermes cockpit is a lightweight control surface for Hermes workflows.

## What it is

- A static HTML/CSS/JS MVP shell
- No build step
- Easy to run locally or extend later

## Local run

From the repository root:

```bash
python3 -m http.server 8000 --directory src
```

Then open http://localhost:8000 in your browser.

## Project layout

- `src/index.html` — cockpit shell markup
- `src/styles.css` — layout and visual styling
- `src/main.js` — placeholder status data and timestamp rendering
- `docs/roadmap.md` — near-term product direction

## Status

The MVP shell is in place and ready for the next layer of Hermes integration.

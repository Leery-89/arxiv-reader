# arXiv Reader

A Chrome side-panel extension that breaks down arXiv papers into a structured
reading guide — research question, method, experimental design, findings,
limitations — with every claim traceable back to the original text.

> 🚧 Work in progress. See [DECISIONS.md](./DECISIONS.md) for the design log.

## Status

| Stage | What | Done |
|---|---|---|
| D1–D2 | Extension scaffold, paper detection | ✅ |
| D3 | Coverage survey (80% HTML, year-independent) | ✅ |
| D3–D4 | Fetch & clean paper text | ✅ |
| D5–D6 | Backend + structured LLM output | ⬜ |
| D7 | End-to-end | ⬜ |
| D8 | Streaming & error handling | ⬜ |
| D9–D10 | Quote traceability | ⬜ |
| D11 | Caching & telemetry | ⬜ |
| D12 | Open source release | ⬜ |
| D13 | Chrome Web Store submission | ⬜ |
| D14 | Evaluation set | ⬜ |

## How it gets the paper text

Primary source is arXiv's HTML rendering (`arxiv.org/html/<id>`), which
preserves section structure and LaTeX math. A survey of 60 papers sampled
across 2012–2026 (`backend/survey.py`) found **~80% coverage, independent of
year** — so roughly one in five papers has no HTML version. Those fall back
to title + abstract via the arXiv API, and the UI says so.

PDF parsing would close that gap but is deferred to v2; see `DECISIONS.md`.

## Install (development)

1. `chrome://extensions` → turn on **Developer mode** (top right)
2. **Load unpacked** → select this folder
3. Open any paper, e.g. `https://arxiv.org/abs/1706.03762`
4. Click the extension icon in the toolbar — the side panel opens

Requires Chrome 114 or later (`sidePanel` API).

## Layout

```
manifest.json      MV3 config — permissions, entry points
content.js         Runs on arxiv.org pages; extracts the paper ID
background.js      Service worker; message relay + side panel control
sidepanel/         The UI
backend/           FastAPI service (D5 onwards)
DECISIONS.md       Design log — why things are the way they are
```

## Setup

```bash
cp .env.example .env    # then fill in your API key
```

`.env` is gitignored. Never commit it.

## License

MIT

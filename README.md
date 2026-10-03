# arXiv Reader

A Chrome side-panel extension that turns an arXiv paper into a structured
reading guide — research question, method, experiments, findings,
limitations — **with every claim traceable to a specific paragraph of the
original text**. Click any paragraph ID and the page scrolls there and
highlights it.

<!-- TODO: screenshot of the side panel next to a paper page -->
![demo](docs/demo.png)

Built as a study in *verifiable* LLM output: the interesting part is not the
summary, it is the evidence check that runs after it.

## What it does

- Detects the paper from any `arxiv.org/abs/`, `/html/`, or `/pdf/` URL
- Fetches arXiv's HTML rendering, parses it into sections and paragraphs,
  keeps LaTeX for formulas, drops citations and references
- Sends the whole paper (long-context, no RAG — see [DECISIONS.md](./DECISIONS.md))
  to DeepSeek with a strict schema: five fields, each with a Chinese summary
  and a list of `{id, quote}` evidence entries
- Streams the result — the summary appears within ~1 s, fields fill in as
  they complete
- **Verifies every quote** against the source paragraph and reports two
  rates: quote hit rate and inline-ID compliance
- Caches by `(paper, prompt hash, model)` so re-opening a paper is instant
  and changing the prompt never serves stale results
- Logs every request to JSONL; `stats.py` turns that into cost, latency, and
  traceability numbers

## Numbers so far

Early single-paper numbers; cross-paper results are in **Evaluation** below.

| | |
|---|---|
| HTML coverage (60-paper survey, 2012–2026) | ~80 %, independent of year |
| Quote hit rate (Transformer paper) | 29 / 29 |
| Inline-ID compliance | 23 / 23 |
| Time to first token | ~1 s (was 9.3 s before streaming) |
| Tokens per paper | ~7–10 k in, ~2.3 k out |
| Cost per paper | ≈ ¥0.015 |
| Prompt v1 → v2 | evidence count 17 → 28, hit rate held at 100 % |

## Evaluation: does the citation actually support the claim?

Checking that a quote *exists* in the paper is easy (98 % pass). Checking that
it *supports* the claim is the real question, so the project has a full
evaluation chain (details in [DECISIONS.md](./DECISIONS.md) D14–D19):

1. **Eval set** — 36 deliberately hard papers in 6 categories (long, odd
   structure, AI-for-science, physics, abstract-only, standard).
2. **Human labels** — 120 claim/evidence units labelled Y / P / N
   (fully / partly / not supported). Human: 50.8 % / 41.7 % / 7.5 %.
3. **Calibrated LLM judge** — five judge models compared against the human
   labels. `deepseek-chat` agrees best (Cohen's κ 0.62) and is the only one
   whose rate of unsupported (N) verdicts matches the humans; stronger models are stricter on
   wording but flag only 1–3 claims as N (humans: 9), and a five-way vote
   lowers κ to 0.52.
4. **Prompt v2 → v3, scored on every claim** (~550 claims, not a sample):

| Claim fully supported by its quotes | v2 | v3 |
|---|---|---|
| Main judge (`deepseek-chat`) | 63.4 % | **81.4 %** |
| Stricter cross-vendor judge (`gpt-6.1-sol`, lower bound) | 39.6 % | **54.5 %** |
| Same, stricter per-ID scoring (`deepseek-chat`) | 52.0 % | 67.5 % |
| Quote exact-match rate | 98.0 % | 96.4–98.7 % (two runs) |

A claim citing several paragraphs is judged on all its quotes together; the
stricter per-ID scoring (every cited paragraph must fully support the claim on
its own) penalises synthesis sentences and is kept as a second view. Both
judges and both scorings agree on the size of the gain (+15 to +18 points). **Which rules matter** — a
leave-one-out ablation (drop one v3 rule at a time, plus an unchanged re-run to
measure noise: 1.4 points between two identical runs):

| v3 without… | Fully supported | Change |
|---|---|---|
| several quotes from one paragraph | 61.9 % | −6.3 |
| quoting the antecedent of "This/It" | 62.5 % | −5.7 |
| every checkable element in some quote | 62.6 % | −5.6 |
| no-exaggeration rule | 67.5 % | −0.7 (noise) |
| both ends of a causal claim | 68.3 % | +0.1 (noise) |

The three citation rules each carry ~6 points; the two wording rules show no
measurable effect on support rate (they target overstatement, which the judge
is less sensitive to). Per-category numbers swing by up to 15 points between
identical runs (~100 claims each), so only totals are reported.

5. **RAG vs full text** (D20). Same prompt, but the model sees only the
   paragraphs retrieved per field (BM25 / embeddings / hybrid) instead of the
   whole paper:

| | Full text | RAG, 50 % of paper | RAG, 25 % of paper |
|---|---|---|---|
| Claims fully supported (`deepseek-chat`) | 81.4 % | 76.9 % | 85.4 % |
| Input tokens / paper | 17.7 k | 9.3 k | 5.4 k |
| Coverage, pairwise wins (full text : RAG)* | — | 16 : 4 | 26 : 4 |
| Key points missed / paper (full text / RAG) | — | 2.2 / 3.4 | 2.0 / 5.3 |

\* Judge compares the two summaries against the full paper, asked in both
orders; a control run of full text vs. itself gives 8 : 7.

RAG claims are just as well supported, but RAG leaves out much more: support
rate alone rewards writing less. A single paper fits in the context window and
a summary has to cover all of it, so the reader keeps sending the full text;
retrieval is kept for follow-up questions.

## How it's built

```
manifest.json       MV3 — sidePanel API, content script on arxiv.org
content.js          Extracts the arXiv ID; on /html/ pages, highlights a paragraph on request
background.js       Service worker — message relay, side panel, jump-to-paragraph
sidepanel/          UI. Reads the SSE stream and renders JSON incrementally as it arrives

backend/
  app.py            FastAPI — /analyze (one-shot) and /analyze/stream (SSE)
  fetcher.py        arXiv HTML → Paper. Fallback to abstract via API. Appendix-first truncation
  models.py         Paper / Section / Paragraph — paragraph IDs are LaTeXML's own
  serialize.py      Paper → text with [S3.p1]-style IDs the model can cite
  prompts.py        The system prompt. Every constraint maps to a DECISIONS.md entry
  llm.py            DeepSeek via the OpenAI-compatible client; JSON mode; streaming variant
  verify.py         Quote hit rate + inline-ID compliance. Only "does the citation exist",
                    not "does it support the claim" — the latter is the eval set's job
  cache.py          File cache keyed on paper + prompt hash + model + temperature
  telemetry.py      One JSONL line per request
  stats.py          Reads the log, prints the numbers above
  survey.py         The coverage survey that decided the fallback strategy
```

Design decisions, with the data that drove each one, are in
[DECISIONS.md](./DECISIONS.md). Prompt versions are in
[prompts_history.md](./backend/prompts_history.md).

## Run it locally

**Backend** (Python 3.11+):

```bash
cd backend
conda create -n arxiv python=3.11 -y && conda activate arxiv   # or any venv
pip install requests beautifulsoup4 fastapi uvicorn openai python-dotenv
cp ../.env.example ../.env         # then put your DeepSeek key in it
uvicorn app:app --reload --port 8000
```

**Extension**:

1. `chrome://extensions` → enable **Developer mode**
2. **Load unpacked** → select this repository's root folder
3. Open a paper, e.g. <https://arxiv.org/abs/1706.03762>, click the toolbar
   icon, click **开始分析**

Requires Chrome 114+ (`sidePanel` API). The extension talks to
`localhost:8000`; change `BACKEND` in `sidepanel/sidepanel.js` and
`host_permissions` in `manifest.json` to point elsewhere.

## Status

| | |
|---|---|
| Extension, fetcher, LLM, streaming, traceability, cache, telemetry | done |
| Chrome Web Store listing | pending |
| Evaluation set, human labels, calibrated judge, prompt v3 | done — see Evaluation above |
| Table parsing, retrieval experiment, follow-up questions | v2 — see DECISIONS.md |

## License

MIT

# Office Intelligence

A source-backed workspace for the London office market. Bring broker readings, macro indicators,
market headlines and research briefs into one English-language interface.

## Features

- One prominent Get latest data & run AI action on the overview.
- Automatic one-time retrieval on entry when no live data has been collected.
- Animated source-by-source progress, cancellation, retrieval/analysis timestamps, duration and recent run history.
- A focused overview with a market interpretation, up to three key readings and source coverage notes.
- Detailed broker readings, macro indicators and market headlines in Market evidence.
- Broker comparisons filtered by metric, submarket and exact period. Figures are independent and never averaged across publishers.
- Market questions using keyword routing or explicitly selected model-assisted analysis.
- Source panels with original definitions, report links, periods and confidence notes.
- Factual or model-assisted briefs with an in-browser preview and Markdown download.
- A Python backend and buildless HTML, CSS and JavaScript frontend, without a CDN or build step.

## Run locally

Python 3.13 is the validated environment. From the project directory on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m office_agent.server
```

On macOS or Linux, use `.venv/bin/python` instead. Open <http://127.0.0.1:8765>.
Use `--port 8766` if the default port is occupied. Stop the server with Ctrl+C.

The browser workspace fetches live sources only. It never seeds from bundled market readings or substitutes
saved source files after a network failure. Unavailable sources stay unavailable and show their errors.
Reporting periods are displayed separately from retrieval times: broker reports update monthly or quarterly,
while macro releases follow their publishers' schedules.

The first visit starts one update if no retrieved data exists. Later visits display the last successful
retrieval and its timestamp; updates are explicit, rather than an uncontrolled polling loop. Concurrent
visitors share a single update and cannot trigger duplicate automatic runs. Retrieved data, AI output and
the last 20 activity records are saved under the ignored `data/` directory so they survive a server restart.

## Configure AI

Copy `.env.example` to `.env` and set credentials locally. Never commit `.env`. Configured providers are
enabled by default: an update can extract broker readings and generate AI analysis, including during the
automatic first run. Model calls can incur charges. Without credentials, macro/news retrieval still works;
AI and broker extraction are shown as unavailable. To disable model calls explicitly:

```powershell
.\.venv\Scripts\python.exe -m office_agent.server --no-ai
```

The overview uses a single **Get latest data & run AI** control to refresh sources before analysis.
Progress and timestamps remain visible; source stages and history are under **Update activity**.
A failed source is not filled with sample data. AI audit flags remain visible, with details under **Source notes**.

| Environment variable | Purpose |
| --- | --- |
| `MIMO_API_KEY` | MiMo credential |
| `MIMO_BASE_URL` | Optional provider endpoint override |
| `OFFICE_AGENT_MODEL` | Default MiMo model |
| `DEEPSEEK_API_KEY` | Optional DeepSeek credential |
| `OFFICE_AGENT_CHAT_LLM` | Chat model in `provider:model` form |
| `OFFICE_AGENT_BRIEF_LLM` | Brief-writing model |
| `OFFICE_AGENT_EXTRACT_LLM` | Broker extraction model |
| `OFFICE_AGENT_EXTRACT_FALLBACK_LLM` | Optional extraction fallback |
| `JEV_API_KEY`, `OFFICE_AGENT_USE_JEV` | Optional structured judgement |
| `OFFICE_AGENT_LOG_INPUT` | Set to `1` to opt into redacted question logging |

MiMo and DeepSeek use their OpenAI-compatible endpoints. Credentials stay on the backend.
Model selection is configuration, not a claim of comparative model quality.

## Command-line interface

```powershell
.\.venv\Scripts\python.exe -m office_agent --no-llm
```

The CLI supports market questions and `/metrics`, `/macro`, `/news`, `/sources`, `/brief`, `/runs`
and `/offline on` for separately supplied source files. Run `python -m office_agent --help` for command-line options.
CLI briefs are written to `output/`; browser briefs remain in session memory and do not overwrite them.

## Architecture

```text
office_agent/
  frontend/       English interface, charts and safe Markdown rendering
  server.py       Local HTTP adapter, browser sessions and bounded jobs
  dashboard.py    Dashboard data assembled from existing skills
  agent.py        Tool selection, permissions and answer orchestration
  skills/         Broker metrics, macro data, news and briefs
  models/         Provider clients and accounting
  checks/         Numeric grounding and optional structured judgement
  domain.py       Metric vocabulary, units and period validation
  runtime.py      Deadlines, cancellation and per-run context
  store.py        SQLite storage with explicit transaction ownership
  references.py   Source register and display URL validation
  security.py     Source fetching policy and credential redaction
  workspace.py    Retrieved-data persistence and grounded overview analysis
docs/
  security.md     Runtime controls and deployment boundaries
```

Model-written answers and briefs receive heuristic numeric checks. A matching number does not prove
that an interpretation or business conclusion is correct. Review definitions, periods, stale flags and
verification warnings before quoting a claim. News contains headlines, not full article text.
Report URLs and layouts may change and require maintenance.

## Server deployment

The included HTTP adapter binds to `127.0.0.1` for local operation. Reuse the frontend and business modules
behind a production HTTP adapter, then add HTTPS, authentication, per-user authorization, shared task and
session storage, rate and spending limits, secret management and dependency scanning. Review the proxy
and Origin policy for the deployed domain. See [security boundaries](docs/security.md).

No license grant is included in this repository. Original publications remain subject to their publishers' terms.

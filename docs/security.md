# Security boundaries

## Local HTTP interface

The dashboard binds to `127.0.0.1`. An exact Host/Origin allowlist rejects unexpected hosts and cross-site
requests. POST endpoints require a per-session CSRF token. Session cookies use HttpOnly and SameSite=Strict;
they identify local browser sessions and are not a user login system.

Only explicitly listed frontend assets are served. Environment files, application source, arbitrary paths
and databases cannot be downloaded through the static handler. The browser never receives credentials.
CSP limits scripts and connections to the same origin, framing is denied, and MIME sniffing is disabled.
Dynamic content uses text nodes and a limited Markdown subset; raw HTML is not executed. External source
links use HTTPS and `noopener noreferrer`.

## Sessions and task budgets

Conversations, jobs and briefs belong to their browser session. The service retains at most 32 sessions,
4 concurrent tasks and 8 recent tasks per session. Each session runs one task at a time. Idle sessions
expire after one hour; a restart clears browser sessions and in-memory downloads. Retrieved market data, analysis and the last 20 activity records are stored in an ignored local workspace file; they are not included in the public repository.

Chat/brief tasks have a 90-second deadline; live update pipelines have a 180-second deadline. Model output is limited to 6,000 tokens per call, with one bounded extraction attempt per report. Nested contexts
inherit cancellation. Guarded writes check cancellation before committing data or replacing output.
Cancellation cannot forcibly terminate an in-flight provider request, which may still be billed.
Browser chat exposes read skills only. Brief generation uses its own endpoint and does not overwrite CLI
output files. Configured AI providers are enabled by default. Manual update/analysis actions and the automatic first run can call them and incur charges. `--no-ai` disables both analysis and broker extraction. Optional model-based headline judgement stays disabled for browser retrieval tasks.

## Source retrieval and data integrity

The browser uses live-only contexts. It does not seed broker rows from bundled files and excludes any older rows whose origin is a bundled source. Failed macro/news retrieval produces an unavailable result, without source-file fallback. Successfully retrieved data can be reused with its retrieval timestamp; this is distinct from its publisher reporting period.

Live source fetching is restricted to configured HTTPS hosts. Requests reject non-public DNS addresses,
validate redirects and cap content size. Environment proxy and netrc settings are not used. DNS checks
are not DNS pinning; stronger isolation requires a deployment egress policy.

Extracted rows are validated for expected fields, finite values, units, periods and source evidence.
PDF table extraction can still misidentify columns and is flagged for review. PDF parsing runs in process;
page/text limits are not a hard CPU or memory sandbox. Use a separate process or container for untrusted
documents in a hosted service.

Transactions are owned by the outer database context; broker imports use nested savepoints. CLI briefs
use temporary files and atomic replacement. A completed write is not undone by a later task failure.
Numeric grounding is heuristic and does not establish every claim's correctness.

## Credentials and logs

Keep secrets in a local `.env` file or a deployment secret manager. Do not commit credentials, provider
responses, databases, generated outputs or logs. Example configuration values are placeholders only.
Question text is omitted from logs by default; a SHA-256 digest is retained for correlation. A digest is
not encryption and can reveal known/repeated inputs. `OFFICE_AGENT_LOG_INPUT=1` opts into redacted input
logging. Redaction cannot identify every sensitive business detail and is not retroactive.

## Docker hosting

The Docker image uses Gunicorn and a WSGI adapter that reuses the endpoint logic. The development server stays
loopback-only. Configure one worker/one replica behind an HTTPS proxy; the internal container port must not be
published on the host. Public Host/Origin checks use a configured exact HTTPS origin, ignoring forwarded-host
headers. Cookies add Secure and responses include HSTS. The health endpoint returns only a static status.

Public paid-analysis/retrieval jobs have a shared daily admission limit stored in SQLite and a refresh cooldown.
The default is 30 jobs per UTC day and 120 seconds between refreshes. One job can make multiple model calls;
these controls do not guarantee a currency-denominated spending cap. Provider-side budget controls are separate.
There is no user login: anyone with the public URL can read the shared market workspace and use the shared
budget. Browser sessions isolate conversations and job downloads, not authenticated user identities. Add an
authentication gateway before using private documents or confidential business data. Sessions and tasks reset
on container restart; the data volume preserves retrieved evidence and admission counts.

The image runs without root and excludes local credentials, collected data, research documents and evaluation
material from its build context. Supply credentials only at runtime. Use dependency scanning, secret rotation,
backups and operational monitoring. Pinned dependencies support repeatability; they are not a vulnerability audit.

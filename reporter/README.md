# BBUG Planning Reporter — Managed Agents (serverless)

A rebuild of the Cherwell planning-application cycling-advocacy reviewer onto
**Claude Managed Agents** with a **serverless control plane** (Vercel + managed
SaaS). Anthropic runs the agent loop; this project supplies the agent config, the
host-side deterministic tools, and the data layer. The full design is in
`/root/.claude/plans/i-want-to-create-hidden-treehouse.md` (the plan), and the
predecessor system lives in `../src` (kept for reference and as the source of the
ported logic).

## Architecture (one line)
Managed Agent (Opus 4.7 + skills) decides what to do → emits `agent.custom_tool_use`
→ this app's **webhook driver** services it host-side against deterministic engines
→ returns **compact JSON**. Raw documents / HTML / OSM never enter the model context.

```
app/api/trigger   create a session (autonomous define_outcome, or cowork)
app/api/webhook   session driver: on idle, run pending custom tools, send results
app/api/classify  cheap Haiku classification (filter / queries / verify)
app/api/tools/search  RAG over Neon+pgvector (the token firewall)
api/tools/*.py    deterministic engines (Cherwell parse, LTN 1/20 route assessment)
api/_pylib/**     proven logic ported verbatim from ../src (parsers, scoring, issues)
agent/*.yaml      the one shared Agent + environment (applied via the `ant` CLI)
skills/**         the "know" layer (uploaded via the Skills API)
db/migrations     Neon + pgvector schema (replaces ChromaDB)
```

## Setup
1. `npm install`
2. Create the data layer: a Neon DB (run `db/migrations/0001_init.sql`), a Voyage
   key, Upstash Redis, Vercel Blob; a hosted Valhalla (e.g. Stadia) key.
3. Copy `.env.example` → `.env` and fill it in.
4. Create the agent + environment (once) and store the IDs:
   ```sh
   npm run env:create     # → REPORTER_ENVIRONMENT_ID
   npm run agent:create   # → REPORTER_AGENT_ID
   ```
5. Register the Anthropic webhook (Console → Webhooks) → your `/api/webhook`,
   subscribe to session status events; store the signing secret as
   `ANTHROPIC_WEBHOOK_SIGNING_KEY`.
6. Deploy to Vercel. Trigger a run:
   ```sh
   curl -XPOST $REPORTER_BASE_URL/api/trigger \
     -d '{"application_ref":"25/01178/REM","mode":"autonomous"}'
   ```

## Self-host vs token balance
Deterministic / heavy / rate-limited work is self-hosted and returns compact JSON
(`fetch_application`, `assess_cycle_route`, RAG search); cheap classification stays
on Haiku **off** the premium agent loop; only judgement (triage interpretation,
mitigation inference, asks, the response) runs on the agent, guided by skills. See
the plan's matrix.

## Seeding the policy KB
Policy is org-level reference data, ingested out-of-band (admin endpoint, not an
agent tool). See `db/seed_policy.example.json` for payload shapes.
```sh
curl -XPOST $REPORTER_BASE_URL/api/policy/ingest \
  -H "x-internal-token: $INTERNAL_TOOL_TOKEN" -H 'content-type: application/json' \
  -d @policy/cherwell_local_plan.json
```
Temporal model: half-open intervals `[effective_from, effective_to)`; a new
revision supersedes the prior open one. The agent passes the application's
validation date as `effective_date` so it cites the revision in force then.

## Status (foundation — steps 0–1 + ingest + policy KB)
Implemented: Python engines ported (Cherwell parse, LTN 1/20 routing/scoring/issues);
tools `fetch_application`, `get_site_boundary`, `assess_cycle_route`,
`ingest_document` (pymupdf born-digital text + Claude-vision OCR → chunk → Voyage →
Neon), `search_application_docs`, `search_policy` (temporal + binding-aware),
`get_policy_section`; admin policy-ingest with revisioning; webhook session driver +
custom-tool dispatch; Haiku classify; trigger (both modes); agent + environment
YAML; four skills; DB schema.

TODO (next): `download_docs` (Blob archival), `export_response` (markdown→PDF/Word);
remaining skills (acquisition, national/regional policy, cycle-provision-assessment,
transport-assessment-review, s106-and-conditions-drafter, advocacy-response);
Upstash rate-limit + escalation queue; eval harness vs the legacy pipeline. The
legacy `../src` stack is **not yet retired** — do that only once this proves out.

> Note: this foundation has not been run against live SaaS from this environment
> (no provisioned Neon/Voyage/Valhalla/Anthropic keys here). Treat external wiring
> as unverified until deployed.

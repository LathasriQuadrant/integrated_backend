# Tableau Pre-Migration Analysis Platform

A stateless, API-driven FastAPI backend that discovers Tableau assets and
runs AI-powered pre-migration analysis ahead of a Power BI migration.

**This platform does not migrate anything.** It discovers Tableau
metadata, builds report-to-data-model relationships, runs AI analysis,
and returns everything as JSON. Nothing is persisted to disk or a
database — every request authenticates to Tableau, does its work in
memory, and signs out.

## Features

**Phase 1 — Discovery**
- Workbook metadata (owner, project, dates, revisions)
- Report assets (workbooks, dashboards, worksheets)
- Usage metadata (view counts, subscriptions, permissions)
- Data model metadata (datasources, databases, schemas, tables, joins, connections, custom SQL)
- Field metadata (dimensions, measures, calculated fields, formulas, data types)
- KPI discovery
- Dependency/lineage metadata (upstream/downstream, shared datasources)
- Component metadata (dashboards, worksheets, filters, parameters, actions)
- Data-model-to-report mapping layer

**Phase 2 — AI Analysis** (OpenAI-powered)
- Least-Used Reports analysis
- KPI Intelligence (duplicate/similar KPI detection, clustering)
- Shared Data Model analysis (Power BI semantic model recommendations)
- Unused Component analysis
- Migration Complexity scoring

## Setup

```bash
# from the directory that CONTAINS the app/ folder
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r app/requirements.txt
cp app/.env.example app/.env
# edit app/.env: set TABLEAU_SERVER and OPENAI_API_KEY
```

## Run

```bash
# from the directory that CONTAINS the app/ folder (app uses absolute
# imports like `from app.api import ...`, so app's parent must be on
# the Python path / be your working directory)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Interactive API docs: `http://localhost:8000/docs`

## Authentication model

This API is stateless. Every discovery/analysis endpoint (except the
individual AI `/analysis/*` endpoints, which accept already-discovered
metadata) takes your Tableau `username`/`password`/`site_content_url`
directly in the request body, signs in to Tableau for the duration of
that single request, and signs out when done. Credentials and Tableau
tokens are **never** written to disk or cached between requests.

```bash
curl -X POST http://localhost:8000/auth/signin \
  -H "Content-Type: application/json" \
  -d '{"username": "myuser", "password": "mypassword", "site_content_url": "mysite"}'
```

## Example: full end-to-end run

```bash
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "username": "myuser",
    "password": "mypassword",
    "site_content_url": "mysite",
    "include_twbx_parsing": true
  }'
```

This runs Discovery across every workbook on the site, normalizes the
metadata, and runs all five AI analyzers, returning one combined JSON
response.

## Example: discovery only, scoped to specific workbooks

```bash
curl -X POST http://localhost:8000/discovery \
  -H "Content-Type: application/json" \
  -d '{
    "username": "myuser",
    "password": "mypassword",
    "site_content_url": "mysite",
    "workbook_ids": ["<workbook-luid-1>", "<workbook-luid-2>"]
  }'
```

## Example: run one AI analyzer against previously-discovered metadata

```bash
curl -X POST http://localhost:8000/analysis/complexity \
  -H "Content-Type: application/json" \
  -d '{"metadata": <output of /discovery>}'
```

## Project structure

```text
app/
├── api/                  # FastAPI routers (auth, discovery, analysis, orchestration)
├── auth/                 # Tableau sign-in + request-scoped session
├── services/
│   ├── discovery/        # REST client, Metadata API (GraphQL) client, TWB/TWBX parsers,
│   │                      # per-facet discovery services, mapping layer, normalizer
│   └── ai/                # OpenAI client wrapper, prompt templates, 5 analyzers
├── models/                # Pydantic request/response schemas
├── utils/                 # XML parsing helpers, complexity scoring helpers
├── config.py               # Environment-variable configuration
├── main.py                  # FastAPI app entrypoint
├── requirements.txt
└── .env.example
```

## Notes on data sources

- The **Tableau REST API** supplies admin metadata: workbook owner/project/dates,
  revisions, views, usage statistics, subscriptions, permissions, and the
  published-datasource inventory.
- The **Tableau Metadata API (GraphQL)** supplies lineage: upstream tables/databases,
  downstream workbooks/dashboards for published datasources.
- **TWB/TWBX parsing** (downloaded via REST, parsed as XML in memory) is the
  only source for calculated-field formulas, joins, custom SQL, LOD
  expressions, table calculations, parameters, filters, and dashboard-to-worksheet
  composition — none of that is exposed by either API.

## Limitations / assumptions

- Requires a Tableau Server/Cloud license with the Metadata API enabled
  for full lineage discovery; discovery degrades gracefully (skips
  lineage) if the Metadata API is unavailable.
- `user_activity` in usage metadata is left as an empty list by default
  since per-user activity requires the optional Tableau Server
  Administrative Views / historical usage tables, which vary by
  deployment; wire in your own query there if you have that data source.
- The AI analyzers require `OPENAI_API_KEY` to be set; discovery-only
  endpoints work without it.

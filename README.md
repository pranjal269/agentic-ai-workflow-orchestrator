# Agentic AI Workflow Orchestrator

Describe an automation in plain language and the system compiles it into a
validated, directed workflow graph and runs it. The core is a
**natural-language-to-workflow compiler**, a **static validation layer**, and a
**graph execution engine** with real HTTP and LLM steps, an expression
sublanguage for wiring data between steps, and recorded run history.

The project runs fully offline with deterministic stubs when no API key is
present, so the application and the test suite need no secrets and no network.

---

## Contents

- [Overview](#overview)
- [How it works](#how-it-works)
- [Architecture](#architecture)
- [Node catalog](#node-catalog)
- [Requirements](#requirements)
- [Backend](#backend)
- [Frontend](#frontend)
- [HTTP API](#http-api)
- [Example: end to end](#example-end-to-end)
- [Offline mode](#offline-mode)
- [Project layout](#project-layout)
- [License](#license)

---

## Overview

A run has three stages:

1. **Plan.** A natural-language goal is sent to an LLM that must answer by
   calling one of two functions: `submit_workflow` (returning a complete
   workflow graph, using the node catalog as its grammar) or
   `request_clarification` (asking for a detail it cannot reasonably assume).
2. **Validate.** The submitted graph is checked statically against the node
   catalog: node types, parameter types and enums, required parameters, data
   bindings that resolve, branch handles that exist, reachability, and cycles
   that are bounded. Validation errors are fed back to the planner for a bounded
   number of repair attempts.
3. **Execute.** The engine walks the graph from the trigger, runs each node for
   real, resolves data bindings and `{{ expressions }}`, follows control-flow
   branches, bounds loops, and records the run.

The LLM calls (planning and the `llm_step` node) go through the `openai` SDK to
any OpenAI-compatible endpoint. The default target is NVIDIA's hosted API with
`nvidia/nemotron-3-super-120b-a12b`.

---

## How it works

```
natural-language goal
        |
        v
   +---------+     submit_workflow / request_clarification
   | Planner |  ------------------------------------------->  OpenAI-compatible endpoint
   +---------+
        |
        v
  Workflow IR  (directed graph: trigger, typed nodes, edges with branch handles)
        |
        v
  +-----------+   errors -> repair loop (bounded by PLANNER_MAX_REPAIR_ATTEMPTS)
  | Validator |
  +-----------+
        |
        v
  +----------------+   real HTTP calls, real LLM calls, expression evaluation,
  | Execution      |   handle-routed branching, bounded loops
  | engine         |
  +----------------+
        |
        v
   Run record  (status, per-node logs, per-node outputs)  ->  in-memory run store
```

Two planning entry points exist:

- `POST /workflow/plan` runs the full loop and can return clarifying questions,
  a validation report, and notes. Use this when a conversational flow is
  acceptable.
- `POST /workflow/generate` never asks questions and always returns a graph. If
  the planner would have asked for clarification, a minimal valid fallback
  workflow is returned instead.

---

## Architecture

| Component | Location | Responsibility |
|---|---|---|
| Workflow IR | `backend/app/schemas/workflow.py` | The graph contract shared by planner, validator, and executor. |
| Node catalog | `backend/app/nodes/` | Typed `NodeSpec` entries: parameters, outputs, and branch handles for each node type. This is the planner's grammar. |
| Planner | `backend/app/planner/` | Goal to Workflow IR via function calling, with a validation-driven repair loop. Falls back to a deterministic stub with no API key. |
| Validator | `backend/app/workflows/validator.py` | Static checks before execution. Errors block a run; warnings do not. |
| Expression evaluator | `backend/app/workflows/expressions.py` | Safe evaluation of `{{ ... }}` against a per-node context. |
| Execution engine | `backend/app/workflows/executor.py`, `runners.py` | Graph traversal, per-node behavior, control-flow routing, loop bounding, run recording. |
| Run store | `backend/app/workflows/runs.py` | Thread-safe, bounded, in-memory history of recent runs (newest first). |
| HTTP API | `backend/app/api/` | FastAPI routes for the catalog, planning, validation, execution, and run history. |
| Reference UI | `frontend/` | A minimal React interface: enter a goal, generate a workflow, view it as a read-only graph, execute it, and read the run log. |

### Design notes

- **Injected callers.** The two runners that reach the network, `http_request`
  and `llm_step`, receive their HTTP client and LLM client as constructor
  arguments. Tests pass fakes; production passes real clients; with no API key
  the LLM client is a deterministic stub.
- **Catalog-driven.** A new node type is one `NodeSpec` plus one runner. The
  validator and executor read the catalog and require no other changes.
- **Data flows separately from control.** Edges carry control flow. Values move
  between nodes through named `bindings`, and string parameters can reference
  bound values with `{{ expressions }}`.

---

## Node catalog

Six node types cover non-linear workflows end to end.

| Type | Category | Key parameters | Outputs | Branch handles |
|---|---|---|---|---|
| `manual_trigger` | trigger | none | `startedAt` | `main` |
| `http_request` | action | `url` (required), `method` (`GET` default; one of `GET`/`POST`/`PUT`/`PATCH`/`DELETE`), `headers`, `query`, `body` | `statusCode`, `headers`, `body` | `main` |
| `llm_step` | action | `prompt` (required), `system`, `model` (default `nvidia/nemotron-3-super-120b-a12b`), `temperature` (default `0`) | `text` | `main` |
| `set` | action | `values` (required object of literals and `{{ expressions }}`) | `values` | `main` |
| `if` | logic | `condition` (required expression) | `result` | `true`, `false` |
| `loop` | logic | `items` (array) or `condition` (while-expression) | `item`, `index` | `each`, `done` |

Node behavior at run time:

- `http_request` issues a real request via `httpx`. The response body is parsed
  as JSON when the content type allows, otherwise returned as text; oversized
  text bodies are truncated at `EXECUTOR_MAX_RESPONSE_BYTES`.
- `llm_step` issues a real chat-completion request to the configured
  OpenAI-compatible endpoint, or returns a deterministic placeholder when no key
  is configured.
- `set` returns an object assembled from its `values` map with every string
  rendered through the expression evaluator.
- `if` evaluates `condition` and activates the `true` or `false` handle. Only
  edges leaving the active handle are followed.
- `loop` activates `each` while iterating an `items` array or while a
  `condition` holds, and `done` afterwards. Loop-back edges must be bounded by
  `max_iterations` on a node in the cycle.

`GET /workflow/catalog` returns this catalog as JSON.

---

The reserved node id `trigger` is the source of the first edge(s). A node's
`max_iterations` is `null` unless the node participates in a cycle.

### Validation

`validate_workflow` reports errors and warnings. Errors block execution;
warnings do not. Covered cases include:

- unknown or misused node and trigger types;
- missing required parameters, wrong parameter types, values outside an enum;
- data bindings that point at an unknown node, at the node itself, or at an
  output field the source node does not declare (warning);
- edges from or to unknown nodes, and edges leaving a branch handle the source
  node does not have;
- nodes unreachable from the trigger (warning);
- cycles with no `max_iterations` guard.

---

## Requirements

- Python 3.11 or newer
- Node.js 18 or newer (for the reference UI)
- Optional: an API key for an OpenAI-compatible LLM endpoint. Without one, the
  planner and `llm_step` use deterministic stubs.

---

## Backend

### Installation

From the repository root:

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
pip install -r requirements.txt
```

### Configuration

Configuration is read from the environment. `backend/app/core/config.py` loads
`backend/.env` at startup if present; real environment variables take
precedence. Copy the template and edit it:

```bash
cp .env.example .env
```

| Variable | Default | Purpose |
|---|---|---|
| `NVIDIA_API_KEY` | unset | API key for the LLM endpoint. Enables the real planner and `llm_step`. |
| `LLM_BASE_URL` | `https://integrate.api.nvidia.com/v1` | OpenAI-compatible base URL. |
| `LLM_ENABLE_THINKING` | `false` | Sends `chat_template_kwargs.enable_thinking` on each request. |
| `PLANNER_BACKEND` | `auto` | `auto` resolves to the real backend when a key is present, otherwise `stub`. Force with `nvidia` or `stub`. |
| `PLANNER_MODEL` | `nvidia/nemotron-3-super-120b-a12b` | Model id used by the planner. |
| `PLANNER_MAX_REPAIR_ATTEMPTS` | `2` | Extra planning attempts after a validation failure. |
| `EXECUTOR_LLM_BACKEND` | `auto` | Same semantics as `PLANNER_BACKEND`, for `llm_step` nodes. |
| `EXECUTOR_LLM_MODEL` | `nvidia/nemotron-3-super-120b-a12b` | Default model for `llm_step` nodes. |
| `EXECUTOR_HTTP_TIMEOUT` | `15` | Seconds allowed per `http_request` node. |
| `EXECUTOR_MAX_RESPONSE_BYTES` | `1000000` | Text-body truncation limit for `http_request`. |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated allowed origins. |

`.env` is git-ignored. `backend/.env.example` is the committed reference.

### Running

Run from the `backend/` directory so the `app` package is importable:

```bash
uvicorn app.main:app --reload
```

The API listens on `http://127.0.0.1:8000`. Interactive documentation is at
`http://127.0.0.1:8000/docs`.

### Tests

```bash
cd backend
pytest
```

81 tests. They run offline with no API key: the planner and `llm_step` use
stubs, and the HTTP and LLM clients are replaced with fakes.

---

## Frontend

A minimal React reference interface built with Vite, React Flow, and Tailwind.
It generates a workflow from a goal, renders the resulting graph read-only,
executes it, and shows the run log.

```bash
cd frontend
npm install
npm run dev
```

The dev server runs on `http://localhost:5173`. Set `VITE_API_BASE_URL` to point
at a backend other than `http://localhost:8000`.

```bash
npm run build     # production build to dist/
npm run preview   # serve the production build
```

---

## HTTP API

Base URL `http://127.0.0.1:8000`. All request and response bodies are JSON.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness probe. |
| `GET` | `/workflow/catalog` | Every node type the planner may use. |
| `POST` | `/workflow/plan` | Plan from a goal; may return clarifying questions. |
| `POST` | `/workflow/generate` | Plan from a goal; always returns a graph. |
| `POST` | `/workflow/validate` | Statically validate a workflow. |
| `POST` | `/workflow/execute` | Validate, run, and record a workflow. |
| `GET` | `/workflow/runs` | Recent runs, newest first (summaries). |
| `GET` | `/workflow/runs/{run_id}` | One recorded run in full. |

### `POST /workflow/plan`

Request:

```json
{ "goal": "Fetch https://example.com and summarise the page" }
```

To answer clarifying questions, send them back with the original goal:

```json
{
  "goal": "Fetch https://example.com and summarise the page",
  "clarifications": [
    { "question": "Which section should be summarised?", "answer": "The whole page" }
  ]
}
```

Response, planned:

```json
{
  "status": "planned",
  "goal": "Fetch https://example.com and summarise the page",
  "backend": "nvidia",
  "questions": [],
  "workflow": { "id": "workflow", "trigger": {}, "nodes": [], "edges": [] },
  "validation": { "ok": true, "errors": [], "warnings": [] },
  "attempts": 1,
  "notes": []
}
```

Response, needs input:

```json
{
  "status": "needs_input",
  "goal": "...",
  "backend": "nvidia",
  "questions": [
    { "id": "q1", "question": "Which channel should the message go to?", "why": "no target was given" }
  ],
  "workflow": null,
  "validation": null,
  "attempts": 0,
  "notes": []
}
```

`backend` is `nvidia` when the request reached the model and `stub` when it was
served offline.

### `POST /workflow/generate`

Request `{ "goal": "..." }`. Returns a Workflow IR object directly, with no
clarification step.

### `POST /workflow/validate`

Request: a Workflow IR object. Response:

```json
{
  "ok": false,
  "errors": [
    { "code": "missing_required_param", "message": "'http_request' requires param 'url'.", "location": "fetch" }
  ],
  "warnings": []
}
```

### `POST /workflow/execute`

Request: a Workflow IR object. The workflow is validated first; an invalid
workflow returns `status: "failed"` with the validation errors as logs and does
not run.

```json
{
  "status": "completed",
  "logs": [
    { "node_id": "fetch", "message": "Ran http_request" },
    { "node_id": "fetch", "message": "-> is_up via 'main'" },
    { "node_id": "is_up", "message": "Ran if" },
    { "node_id": "is_up", "message": "-> summarise via 'true'" },
    { "node_id": "summarise", "message": "Ran llm_step" }
  ],
  "results": {
    "trigger": { "startedAt": "2026-09-08T10:00:00+00:00" },
    "fetch": { "statusCode": 200, "headers": {}, "body": {} },
    "is_up": { "result": true },
    "summarise": { "text": "..." }
  },
  "run_id": "run_1a2b3c4d5e6f",
  "started_at": "2026-09-08T10:00:00+00:00",
  "finished_at": "2026-09-08T10:00:01+00:00"
}
```

If a node raises at run time, the response is `status: "failed"` with an `Error`
log entry and the partial `results` gathered so far. A hard ceiling of 500 node
steps protects against a mis-specified graph.

### `GET /workflow/runs`

Query parameter `limit` (default `50`). Returns summaries newest first:

```json
[
  {
    "id": "run_1a2b3c4d5e6f",
    "workflow_id": "workflow",
    "goal": "Fetch a status endpoint and summarise it when the service is up",
    "status": "completed",
    "started_at": "2026-09-08T10:00:00+00:00",
    "finished_at": "2026-09-08T10:00:01+00:00",
    "node_count": 3
  }
]
```

### `GET /workflow/runs/{run_id}`

Returns the full record, including per-node `logs` and `results`. Responds
`404` if the id is unknown. The store keeps the 200 most recent runs in memory;
it is not persisted across restarts.

---

## Example: end to end

Start the backend, then:

```bash
# 1. Plan a workflow from a goal
curl -s -X POST http://127.0.0.1:8000/workflow/plan \
  -H 'Content-Type: application/json' \
  -d '{"goal": "Fetch https://api.github.com/repos/python/cpython and report the star count"}'

# 2. Execute a workflow (send a Workflow IR object as the body)
curl -s -X POST http://127.0.0.1:8000/workflow/execute \
  -H 'Content-Type: application/json' \
  -d @workflow.json

# 3. List recent runs
curl -s http://127.0.0.1:8000/workflow/runs

# 4. Inspect one run
curl -s http://127.0.0.1:8000/workflow/runs/run_1a2b3c4d5e6f
```

On Windows PowerShell, use `Invoke-RestMethod` or the interactive docs at
`/docs`.

---

## Offline mode

With no `NVIDIA_API_KEY`:

- `PLANNER_BACKEND` and `EXECUTOR_LLM_BACKEND` resolve to `stub`.
- The planner returns a fixed research-style workflow
  (`trigger -> http_request -> if -> {llm_step | set}`).
- `llm_step` returns `"[offline-llm:<model>] <prompt>"`.
- `http_request` still performs real network calls.

This keeps `uvicorn` and `pytest` working with no configuration. Provide a key
and the `auto` backends switch to the real endpoint with no code change.

---

## Project layout

```
backend/
  app/
    api/            FastAPI routes (health, workflow)
    core/           settings and the shared OpenAI-compatible client
    nodes/          node catalog and the typed NodeSpec model
    planner/        goal -> validated Workflow IR (function calling + repair loop)
    schemas/        Workflow IR and request/response models
    workflows/      validator, expression evaluator, execution engine, run store
    main.py         FastAPI application
  tests/            catalog, validator, expressions, executor, planner, runs, backends
  requirements.txt
  .env.example
frontend/
  src/
    components/     goal input, execution log panel
    hooks/          workflow generate/execute state
    pages/          main screen
    services/       API client
    workflow/       React Flow canvas
    types/          shared JSDoc typedefs mirroring the backend schema
  package.json
README.md
```

---

## License

This repository does not yet include a license file. Until one is added, all
rights are reserved by the author.

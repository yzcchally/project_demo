# SilverGuide task API

Base: `http://127.0.0.1:8000`. OpenAPI: `/docs`. API version: 2.0. Compatibility paths `/tasks`, `/tasks/{id}/approve`, `/tasks/{id}/control`, and speech endpoints remain available.

| Method/path | Payload or result |
| --- | --- |
| `POST /api/tasks` | `{instruction, request_key, assistance_mode?, scenario_id?, third_party_assistance?}`; creates one natural-language goal. |
| `GET /api/tasks/latest` | Latest durable task; `null` before the first task. |
| `GET /api/tasks/{id}` | Current task, draft, proof flags, interaction nonces and user-facing state. |
| `POST /api/tasks/{id}/clarify` | Explicit recipient/alias, optional subject/body/URL and `save_contact`. |
| `POST /api/tasks/{id}/qr` | `{url, scan_nonce}` from the camera result. |
| `PATCH /api/tasks/{id}/draft` | Explicit draft edits; invalidates approval and re-prepares. |
| `POST /api/tasks/{id}/confirm` | `{draft_digest, approval_nonce, confirmed:true}`. |
| `POST /api/tasks/{id}/pause`, `/resume`, `/cancel` | Controls; `/control` also accepts `{action}`. |
| `POST /api/tasks/{id}/control` | `retry_prepare` before sending; `verify_only` after an attempted send. |
| `POST /api/tasks/{id}/received` | Explicit manual receipt confirmation bound to current digest. |
| `GET /api/tasks/{id}/events?after=0` | Ordered events, at most 100 per page. |
| `GET /api/tasks/{id}/explain` | Deterministic explanation of the current actual state. |
| `GET/PUT /api/contacts`, `DELETE /api/contacts/{alias}` | Explicit alias/address mappings. |
| `GET /api/system/graph` | Mermaid of the compiled LangGraph. |
| `GET /api/evaluation` | Pilot outcomes with unmeasured rates represented by `null`. |
| `GET /speech/status`, `POST /speech/transcribe` | Local voice readiness and short base64 audio transcription. No task execution or approval. |

## WebSocket

`ws://127.0.0.1:8000/ws/tasks/{id}?after=<cursor>` first sends `{event_type:"snapshot", task, events, cursor}`. Later messages contain `{event_type:"task_progress", task, event, cursor}`. Heartbeats contain `event_type:"heartbeat"`. Reconnect with the last cursor; consumers ignore duplicates and stale task timestamps.

## States and decisions

Human waits: `needs_clarification`, `awaiting_qr`, `awaiting_confirmation`. Active stages include `understanding`, `preparing`, `opening_webpage`, `preparing_draft`, `sending`, `verifying`, `paused`. Terminal/reportable states include `completed`, `failed`, `cancelled`, `outcome_unknown`, `interrupted`; changed drafts use `needs_review`.

The same request key/content returns the same task. Reusing a key with different content is rejected. Repeated confirmation cannot repeat an external send. Stale digest/nonce, invalid contacts/URLs and actions outside the current phase are rejected. Unsupported goals stop with an explanation.

Resuming a paused QR wait rotates `scan_nonce`. Clients reopen the scanner only after the prior scan activity has returned, bind its callback to the launch task/nonce, and discard old results.

## Pilot/comparison metadata

`assistance_mode` is `silverguide` or `basic`; both retain the same planner, executor and safety gates. Basic mode shortens feedback. Scenario labels are N01–N04, R01–R04 and S01–S04. `third_party_assistance` remains unknown unless explicitly recorded; a developer-assisted run is not an independent-user study result.

# SilverGuide task API

Base: `http://127.0.0.1:8000`. OpenAPI: `/docs`. API version: 2.0. Compatibility paths `/tasks`, `/tasks/{id}/approve`, `/tasks/{id}/control`, and speech endpoints remain available.

| Method/path | Payload or result |
| --- | --- |
| `POST /api/tasks` | `{instruction, request_key, assistance_mode?, scenario_id?, third_party_assistance?, demo_case_id?, demo_fault?}`; creates one natural-language goal. |
| `GET /api/tasks/latest` | Latest durable task; `null` before the first task. |
| `GET /api/tasks/{id}` | Current typed task, proof flags, interaction nonces and user-facing state. Route tasks expose `transit_request`/`transit_result`, not a mail draft. |
| `POST /api/tasks/{id}/clarify` | Explicit recipient/alias, optional subject/body/URL and `save_contact`. |
| `POST /api/tasks/{id}/qr` | `{url, scan_nonce, scan_source?}` from the current scan round. `scan_source` defaults to `in_app_camera`; a user-confirmed Android text share uses `external_share`. |
| `PATCH /api/tasks/{id}/draft` | Explicit draft edits; invalidates approval and re-prepares. |
| `POST /api/tasks/{id}/confirm` | `{draft_digest, approval_nonce, confirmed:true}`. |
| `POST /api/tasks/{id}/pause`, `/resume`, `/cancel` | Controls; `/control` also accepts `{action}`. |
| `POST /api/tasks/{id}/control` | `retry_prepare` before sending; `verify_only` after an attempted send. |
| `POST /api/tasks/{id}/received` | Explicit manual receipt confirmation bound to current digest. |
| `GET /api/tasks/{id}/events?after=0` | Ordered events, at most 100 per page. |
| `GET /api/tasks/{id}/explain` | Deterministic explanation of the current actual state. |
| `GET/PUT /api/contacts`, `DELETE /api/contacts/{alias}` | Explicit alias/address mappings. |
| `GET /api/system/graph` | Mermaid of the compiled LangGraph. |
| `GET /api/system/demo-capabilities` | Available T01–T20 IDs and explicit recovery-demo support. No device action. |
| `GET /api/evaluation` | Pilot outcomes with unmeasured rates represented by `null`. |
| `GET /speech/status`, `POST /speech/transcribe` | Local voice readiness and short base64 audio transcription. No task execution or approval. |

## WebSocket

`ws://127.0.0.1:8000/ws/tasks/{id}?after=<cursor>` first sends `{event_type:"snapshot", task, events, cursor}`. Later messages contain `{event_type:"task_progress", task, event, cursor}`. Heartbeats contain `event_type:"heartbeat"`. Reconnect with the last cursor; consumers ignore duplicates and stale task timestamps.

## States and decisions

Human waits: `needs_clarification`, `awaiting_qr`, `awaiting_confirmation`, `awaiting_place_choice`. Active stages include `understanding`, `preparing`, `opening_webpage`, `preparing_draft`, `sending`, `verifying`, `planning_transit`, `reading_transit`, `validating_transit`, `viewing_transit`, `paused`. Terminal/reportable states include `completed`, `failed`, `cancelled`, `outcome_unknown`, `interrupted`, `partial`, `no_route`; changed drafts use `needs_review`.

The same request key/content returns the same task. Reusing a key with different content is rejected. Repeated confirmation cannot repeat an external send. Stale digest/nonce, invalid contacts/URLs and actions outside the current phase are rejected. Unsupported goals stop with an explanation.

Task snapshots may additionally include `recovery_count`, `last_recovery` and
`recovery_events` (last 20). Recovery events contain `time`, `stage`, `action`,
`outcome`, `attempt` and a user-facing `message`. Outcomes include `waiting`,
`attempting`, `recovered`, `skipped`, `blocked` and `exhausted`. The task remains
in its actual browser/draft phase, and progress is streamed over the same socket.
Recovery never grants approval or retries a send. See [recovery test cases](RECOVERY_TEST_CASES.md).

Resuming a paused QR wait rotates `scan_nonce`. Clients reopen the scanner only after the prior scan activity has returned, bind its callback to the launch task/nonce, and discard old results.

External scan shares are treated as user-supplied links, not evidence that a QR code was actually scanned. Before leaving the app to scan, the user selects “准备使用手机扫码”, capturing the current task and nonce. The app accepts a single HTTPS text URL, shows it for user confirmation, and continues only when that captured task and nonce still match the current snapshot. The server independently validates the URL and source; an expired nonce or invalid source cannot advance the task. Existing browser verification and mail approval still apply.

## Pilot/comparison metadata

The App may attach `demo_case_id` (T01–T20); T21 is rejected. The App shows 19
examples, T02–T20; T01 remains compatible with older clients and the backup
web-only check. The App first separates map and mail examples, then filters each
by recommended, normal, checks, or recovery. These UI classifications are local
example metadata, not backend execution modes:

| App group | Normal | Checks | Recovery | Recommended |
| --- | --- | --- | --- | --- |
| Map, T13–T20 | T13 explicit endpoints; T14 verified current location to 广东省博物馆 | T15 ambiguous origin; T16 ambiguous destination; T17 cancel | T18 clarify origin and continue the same task; T19 pause/resume or safely requery; T20 manually restore phone network and requery | T13, T15, T18 |
| Mail, T02–T12 | T02 complete scan-to-mail workflow | T03–T11 draft, recipient, URL and task-control checks | T12 composer interruption | T02, T04, T12 |

T13–T20 use the ordinary route workflow and never enable the T12 mail fault.
T15–T20 add no synthetic fault, automatic network toggle or alternative executor.
T19 can stop with a requery instruction when the page cannot safely resume.
T20 requires the person to disconnect/reconnect the phone network while keeping
the backend and USB connection available; cached Amap results mean the intended
network failure was not reproduced. Availability of an example does not certify
a real-device run. Only T12 paired with
`demo_fault:"composer-background"` can request the one-shot real HOME interruption.
Any other case with that fault, or T12 without it, is rejected.
The same request key cannot be reused with changed demo configuration.
`demo_fault_injected` is registered before HOME; `demo_fault_status:"observed"`
requires actually reading a screen outside the composer. Sending and verification
never arm the interruption. Choosing a local example does not create a task.

`assistance_mode` is `silverguide` or `basic`; both retain the same planner, executor and safety gates. Basic mode shortens feedback. Scenario labels are N01–N04, R01–R04 and S01–S04. `third_party_assistance` remains unknown unless explicitly recorded; a developer-assisted run is not an independent-user study result.


## Amap public-transit workflow

This is the existing local-computer/USB architecture: the companion still uses
HTTP/WebSocket through `adb reverse tcp:8000 tcp:8000`. It does not perform
standalone-phone planning. The backend operates the installed Amap GUI; no route
REST service or recorded route result replaces a query.

### Creation and route snapshots

Use the existing `POST /api/tasks` natural-language endpoint, for example:

```json
{
  "instruction": "在广州，从广州塔去体育西路，坐地铁或公交，尽量少换乘。",
  "request_key": "transit-classroom-unique-001",
  "assistance_mode": "silverguide",
  "demo_case_id": "T13"
}
```

`demo_case_id` is optional. A different run should use a fresh request key;
repeating the same key and content returns the original task. Goal understanding
returns `task_type:"plan_transit"` and a separate `transit_request`:

```json
{
  "origin": "广州塔",
  "destination": "体育西路",
  "city": "广州",
  "preference": "fewer_transfers",
  "use_current_location": false
}
```

Preference values are `recommended`, `fewer_transfers`, or `less_walking`.
An explicit origin overrides Amap's default current-position origin. A destination
alone may attempt the actual app location, but an unavailable, denied, stale or
unverifiable fix leads to a request for an explicit origin. An unbound “家里”
requires clarification. The runtime never substitutes a classroom origin for a
real current position.

### Current-location evidence

When `origin:""` and `use_current_location:true`, the adapter opens Amap's
[official My Location URI](https://lbs.amap.com/api/amap-mobile/guide/android/location):
`androidamap://myLocation?sourceApplication=SilverGuide`. This is a real GUI
entry, not a location or route JSON service. It must read the current official
location card's actual address title and city, together with its current-position
caption and near-distance indicator. A generic “我的位置” label or a nearby POI
list is insufficient.

The resulting origin retains `source:"current_location"`, with separate field
`evidence_refs` for the actual name, address and city. A corresponding current-query
`location` evidence record carries typed `location_status`:

| Field | Required meaning for verified current location |
| --- | --- |
| `verified`, `enabled`, `permission_granted`, `app_access_recent` | True; current Android user, current-user runtime authorization and recent allowed Amap access were checked. |
| `provider` | An observed `gps`, `network` or `fused` fix. |
| `age_seconds` | Actual system fix age, at most 120 seconds by default. |
| `accuracy_m` | Actual positive horizontal accuracy, at most 100 meters by default. |
| `is_mock` | Explicit false for the inspected system fix. |
| `checked_at`, `fix_time` | Device check time and actual fix time; never UI observation time used as a fix timestamp. |

The read-only helper inspects `cmd location is-location-enabled --user`, that
user's runtime permission block in the Amap package dump, user-scoped `FINE_LOCATION`
appops, and current-user provider last-fix metadata. Device `date +%s` and uptime
check elapsed/wall-clock consistency. Another profile's denied/granted permission
cannot determine this user's result. Unknown or inconsistent metadata cannot
certify location health. Additional typed diagnostic fields are `reason`,
`user_id`, `appops_mode`, `app_access_age_seconds` and `clock_basis`; the adapter
may keep them only in the local audit and include the necessary health subset
in route evidence.

System dump text stays in memory; this helper does not parse, return or persist
coordinates, request a location, change system settings, grant permission or
create mock data. GUI address/city verification remains independent from system
health, and neither alone certifies a complete route.

When a current-position candidate needs human confirmation, it uses the existing
`place` endpoint and additionally carries `source:"current_location"`, the current
`query_id`, actual name/address/city, and their evidence references. Confirmation
keeps `origin:"" / use_current_location:true`; it does not silently turn GPS into
an explicit typed origin or disable the independent source check. Requery clears
old location choices and obtains a fresh generation. Denied/disabled/stale/mock/
inaccurate/unverifiable location or unreadable/mismatched city leads to route
clarification for an explicit origin/city. Actual full GPS-route conclusions are
recorded in `AMAP_ACCEPTANCE.md`, not inferred from this contract.

Route tasks retain `id`, `request_key`, `state`, timestamps, `message` and the
existing event interface. Additional route fields are:

| Field | Meaning |
| --- | --- |
| `query_id` | Current query generation; renewed on requery. |
| `transit_request` | Confirmed parameters for this query. |
| `place_choice` | Pending query-bound candidate selection, or `null`. |
| `transit_choices` | Already selected place facts; never an old screen row index. |
| `transit_result` | Independently checked structured result, or `null` before acquisition. |
| `route_verified` | True only for a complete, current, evidence-supported route. |
| `transit_executor` | Frozen executor for this query: `rules` (default, including optional single-step assistance) or the explicitly selected legacy `autoglm` loop. This field alone does not assert that assistance was used. |
| `transit_agent_step`, `transit_agent_last_action`, `transit_agent_events` | AutoGLM step number, sanitized latest action metadata and at most 20 recent summaries. No API key, raw thinking or Type text. |
| `transit_explanation` | Deterministic narration from the same checked facts used in cards. |
| `transit_history` | Earlier query results and their query/source references after requery. |
| `cancel_requested_at`, `cancel_worker_finished_at`, `cancel_delay_seconds` | Measured cooperative cancellation timing when the route worker exits. |

The route snapshot deliberately has no required `draft`, `draft_digest`, mail
approval nonce or recipient. Its `send_attempts` stays zero; mail proof flags do
not certify route success. Existing historical email records keep their format.

### Human decisions and route actions

All paths below use `POST /api/tasks/{task_id}/transit/…`. Body models reject
unknown fields; `query_id` must be 8–80 characters and match the current task.
The normal response is `{ok:true, task, message}` unless specified below.
Invalid phases, stale generations or ownership conflicts return HTTP 409;
invalid request structures return HTTP 422.

The process default `SILVERGUIDE_TRANSIT_EXECUTOR` is copied to the task when a
new route query starts. Clarification/place-choice/pause resumes retain that
query's saved executor, even if the default later changes. Explicit `requery`
starts a new query using the current process configuration and archives the
prior result together with its executor. Historical tasks without the field
remain `rules`. Changing `.env` requires restarting the backend; the App does
not currently expose a model-selection toggle. See [AutoGLM configuration](AUTOGLM_TRANSIT.md).

AutoGLM API/config/action failures are real task failures; the runtime does not
start a rules query as fallback. Both modes run through the same task worker,
ownership checks, current-query validation and result classifier. Source viewing
uses the existing independently checked viewer and is not a new rules query.
Route events include `transit_executor` and, for AutoGLM, the step/latest-action
summary. A GUI-agent `Finish` requests independent collection/validation; it
cannot itself set `route_verified:true` or COMPLETE.

For `rules`, the optional `SILVERGUIDE_AUTOGLM_ASSIST_ENABLED` feature does not
change the executor field. Its per-query `autoglm-assist.jsonl` local evidence
records the trigger, request, checked candidate, execution and postcondition;
an absent log means the assist path was not used. See
[rule-led AutoGLM assistance](AUTOGLM_ASSIST.md). A controlled test trigger is
identified in that log and must not be reported as a naturally occurring fault.

| Suffix | Body | Behaviour |
| --- | --- | --- |
| `clarify` | `{query_id, origin?, destination?, city?, use_current_location?, preference?}` | Resumes a `needs_clarification` route; at least one supplied field is required. |
| `place` | `{query_id, choice_nonce, candidate_id}` | Resumes `awaiting_place_choice` using one currently offered candidate. |
| `requery` | `{query_id, preference?}` | Starts a fresh generation after a finished/partial/no-route/failed/interrupted query; keeps previous result sources. |
| `ask` | `{query_id, question}` | Answers from the current verified result; returns `{ok:true, task_id, query_id, answer}` without device actions. |
| `show` | `{query_id}` | Acquires the device worker to return to Amap for inspection; does not start in-trip navigation. |

A `place_choice` snapshot has this structure:

```json
{
  "query_id": "current-query-generation",
  "choice_nonce": "single-use-random-value",
  "role": "destination",
  "candidates": [
    {
      "candidate_id": "candidate-from-this-observation",
      "name": "本次高德实际显示的地点名称",
      "address": null,
      "city": null
    }
  ]
}
```

Candidate address/city fields are nullable when the screen did not expose them.
The client submits the ID and nonce, not an arbitrary name or list index.
The runtime checks task, query, phase, nonce and candidate membership, then the
adapter re-observes the actual screen before locating the selected place.

Stable native search-result cards may be offered for explicit confirmation when
their displayed name differs from the user's wording. This does not create an
automatic alias. Only a valid current choice updates that role's canonical
`transit_request` name. A current-location origin confirmation is the exception:
it keeps the empty-origin location mode and its independent source check. The
original instruction, `transit_original_request`, and
query-bound `transit_place_selections` audit are retained. During clarification,
only actually changed place fields invalidate their corresponding selections;
changing city invalidates both. Submitting an unchanged destination while adding
an origin preserves the confirmed destination name and address constraints.
Consumed or previous-query selections cannot affect a later query.

The `ask` endpoint supports “第一步怎么走？”、“我应该在哪一站下车？” and
“需要换乘几次？”. Multiple riding segments are listed separately when needed.
A PARTIAL/unverified route cannot produce an asserted complete-route answer.
“换一个步行更少的方案” requires `requery` with `preference:"less_walking"`;
changing the narration alone is not a new or better route.

### Results, evidence and ownership

`transit_result` includes `task_id`, `query_id`, `route_id`, `source_app:"高德地图"`,
`queried_at`, `collected_at`, confirmed `origin`/`destination`, `preference`,
`applied_filter`, `selection_basis`, and nullable `duration_minutes`,
`walking_meters`, `transfers`, `fare_yuan`. Ordered `segments` have `mode`
(`walk`, `bus`, `subway`) and the actually read `line`, `direction`, `board`,
`alight`, `stops`, `instructions`, `entrance`, `exit` and measurements.
Unknown optional facts stay null. Every asserted fact uses field-level
`evidence_refs`; `derived` records numeric/transfer calculations.

Classification is `COMPLETE`, `PARTIAL`, `NO_ROUTE` or `FAILED`, with
`missing_fields` and optional `failure_reason`. COMPLETE requires independently
checked endpoint/selected-place facts and complete detail acquisition, not just a
list summary or a model's completion claim. Missing critical riding facts or an
unfinished detail collection produces PARTIAL and `route_verified:false`.
A NO_ROUTE classification requires an actual Amap no-transit message.
Missing noncritical fare data alone does not invalidate an otherwise complete
route. Full raw XML/screenshots stay in the task evidence directory; snapshots
carry structured facts, short literal excerpts and local evidence references.

The same WebSocket snapshot/progress channel carries the route fields. Clients
must match both `task_id` and `query_id` before displaying asynchronous answers,
replaying speech, applying selections or accepting route results. States
`partial` and `no_route` are terminal alongside the existing completion/failure
states. Route `completed` does not open the email receipt/completion dialog:
results remain visible until the user explicitly prepares a new task.

All map GUI work, requery and detail-view actions reuse the engine's single
worker/lock. They cannot run alongside an email or legacy AI operation. Pause,
resume and cancellation use the existing controls; resuming a route invalidates
old observed targets and requires fresh observations. Cancellation is cooperative:
a running model/system call may return later, but its result is then discarded
before further clicks, result commit or a late foreground-app switch. The API
does not promise instantaneous cancellation of blocking external calls.

The detail-view adapter checks the currently visible Amap route again. With a
long multi-leg itinerary, the visible screen may contain only a later segment;
if it cannot match this query's necessary route facts, it stops safely and asks
for a new query rather than presenting that page as verified current details.

Mail `/confirm`/`approve`, `/received`, draft edit, `/qr`, ordinary mail
`/clarify`, `retry_prepare`, and `verify_only` reject route tasks. The normal mail
digest/nonce binding and single-send rule remain unchanged. Route evaluation uses
`route_verified` plus a matching COMPLETE `transit_result`, never mail send flags.
This contract describes the implementation; actual device-query conclusions and
unverified conditions are recorded separately in `AMAP_ACCEPTANCE.md`.

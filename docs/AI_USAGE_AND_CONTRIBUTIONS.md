# AI use, review and contribution record

## Runtime AI

- Structured intent and screen-perception candidates use the locally configured model provider. Provider/model names, task ID, stage, timing, token counts and input hashes are logged in `.run/workflows/model-calls/calls.jsonl`.
- Current configured pilot model: `deepseek-v4-flash-vision-exp`; `SILVERGUIDE_TEXT_MODEL`, `SILVERGUIDE_VISION_MODEL` and `SILVERGUIDE_MODEL_BASE_URL` allow replacement.
- Speech recognition uses a pinned, downloaded Whisper base model locally. Model provenance is stored beside the model; recordings are temporary.
- No model training or fine-tuning has been performed. Confirmation, nonce/digest validation, side-effect recording and final evidence checks are deterministic.

## Development assistance

Code, refactoring, test generation, debugging and documentation were assisted by Codex in the shared local workspace. Do not attribute generated work to an individual teammate without their actual implementation/review record. The user supplied the project baseline, device access and test accounts, personally reviewed email confirmations and reported receipt/voice results. Automated checks and real-device debugging provide engineering evidence; they are not an independent human code review.

## Four-person record to complete

| Member | Actual implementation | Design/review | Tests performed | Evidence |
| --- | --- | --- | --- | --- |
| A / name pending | To be recorded by team | To be recorded | To be recorded | Issue/PR/commit links |
| B / name pending | To be recorded by team | To be recorded | To be recorded | Issue/PR/commit links |
| C / name pending | To be recorded by team | To be recorded | To be recorded | Issue/PR/commit links |
| D / name pending | To be recorded by team | To be recorded | To be recorded | Issue/PR/commit links |

GitHub CI configuration is included. Remote CI execution, PR review, merge and team submission must be recorded when actually performed; local successful checks are not remote CI results.

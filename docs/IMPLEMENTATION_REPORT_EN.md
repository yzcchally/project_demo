# SilverGuide: Implementation and Pilot Validation

## Scope

SilverGuide is a controlled Android-plus-computer prototype for older adults who can use basic phone features but need help with unfamiliar cross-application tasks. Its main task is to acquire a real QR URL, open and verify the webpage, prepare an email, obtain explicit user approval and verify the sending result. It provides concise explanations and user control rather than step-by-step phone training.

## Implementation

The companion uses Kotlin and Jetpack Compose. FastAPI and Pydantic validate task and human-decision interfaces. LangGraph, with SQLite checkpoints, drives understanding, clarification, QR acquisition, device preparation, draft review and result verification. A local Mobilerun adapter observes UI trees and screenshots and executes observed taps; a restricted Unicode bridge supports the tested Huawei editor. SQLite and JSONL preserve task/effect evidence. WebSocket progress feeds an Android foreground notification with pause, resume and cancellation controls.

The intent model produces a bounded task schema. Recipient addresses and URLs require grounding in explicit user input, an explicit contact mapping, actual QR return or browser observation. The GUI model proposes only typed preparation actions. A separate guard checks target IDs, field roles and literal expected values. Neither model is given approval or send tools.

The native speech recognizer did not yield text on the tested Huawei device. The implementation therefore uses short AAC recordings and computer-local faster-whisper, while Android TTS provides Chinese progress. QR acquisition uses the companion's ZXing scanner. These are documented adaptations from the proposal's preferred system recognizer/existing scanner.

## Safety and recovery

Approval binds task ID, current draft digest and a fresh nonce. Draft edits invalidate the old approval. The native composer is checked again immediately before sending. The runtime durably registers one send attempt before dispatch. A crash or uncertain network result never causes automatic replay. Independent Sent verification checks the recipient, subject, full reconstructed body and unique task marker. Manual recipient confirmation is stored separately from automatic Sent evidence.

Cancellation and pause are checked before later operations. After a resumed foreground change, the adapter restores and re-observes the relevant context. Unknown outcomes retain a verification-only path. Client WebSockets replay events by cursor after reconnection. Unsupported or ungrounded requests stop or ask a clear question.

## Evidence and evaluation limitations

The repository contains pytest unit/integration tests, Android JVM tests, build/lint checks, real-device evidence and a scenario/metric harness. Exact current counts and completed pilot cases are maintained in `ACCEPTANCE.md` and local acceptance records. Device-double results must not be reported as 36 real-phone trials. The report generator retains failed, cancelled and UNKNOWN cases and represents unmeasured rates as null.

Previous pilot validation established real QR propagation, Chinese Huawei drafts, explicit human approval, recipient confirmation, independent Sent verification, pause/resume, cancellation, reconnection and Chinese voice input/output. The refactored agent's specific pilot evidence is recorded separately; earlier successful runs are not silently relabelled as new-stack results.

The refactored real-phone pilot accepted an actual QR scan, resolved an explicitly supplied contact alias, verified the full native browser address and used a real VLM call to recognize the composer. After the user personally approved in the companion, the runtime dispatched one send and independently matched the external Sent detail. The recipient then confirmed receipt with the correct subject and body. The first browser check had stopped safely because Huawei hid its toolbar; a tested adaptation restored the address bar and retried the same task without inventing QR data or sending twice. This developer-assisted pilot is not an independent-user trial or evidence that all 12 scenarios pass.

No older-adult usability study, B0/B1 benefit estimate or general-device compatibility claim has been established. The system's engineering mechanisms support such a study, but improvements in independence, understanding or workload require actual consenting participants and a fixed evaluation protocol.

## Delivery and remaining external decisions

Run instructions, architecture, API contract, AI-use record, contributor template, evaluation scenarios and a two-minute demo script are included. Test account recordings and local databases are not committed. Four member names/contributions, official deadlines, the course's scanner-count interpretation and final slides/report approval remain team/instructor decisions.

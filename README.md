# SilverGuide — An Explainable Mobile GUI Agent for Older Adults

A controlled Android + local-computer prototype: natural-language goal → clarification → real QR → webpage → reviewed email → evidence-based result.

The implementation uses Kotlin/Compose, FastAPI/Pydantic, LangGraph with SQLite checkpoints, a local Mobilerun adapter, constrained LLM/VLM candidates, WebSocket foreground controls, and local Chinese speech recognition. Human approval and single-attempt sending remain deterministic.

- [运行与开发说明 / Setup](docs/README.md)
- [Architecture](docs/ARCHITECTURE.md)
- [API contract](docs/API_CONTRACT.md)
- [Measured acceptance scope](docs/ACCEPTANCE.md)
- [Demo and evaluation protocol](docs/DEMO_AND_EVALUATION.md)
- [Implementation report](docs/IMPLEMENTATION_REPORT_EN.md)
- [AI use and contribution record](docs/AI_USAGE_AND_CONTRIBUTIONS.md)

The prototype needs an authorized USB-connected test phone and a running local backend. Current engineering evidence does not establish a general-device guarantee or an older-adult usability benefit.

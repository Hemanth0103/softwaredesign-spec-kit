# Research

## Decisions

**PostgreSQL + pgvector with hybrid retrieval.** Store governance metadata and embeddings together. Retrieval first filters `approved`, `active`, effective, campus/program/term-compatible revisions in SQL, then combines full-text and vector candidates. Start exact search for the small corpus; add cosine HNSW after measured latency requires it. This preserves perfect recall early and makes retirement atomic; a separate vector database would duplicate lifecycle state.

**Provider-neutral AI adapter.** FastAPI exposes internal `embed` and `generate_grounded_answer` adapters selected through deployment configuration. Each immutable chunk records embedding model/version/dimension, and only compatible vectors are compared. Generation receives retrieved excerpts and structured output is independently citation-checked. A fixed vendor was rejected because none is specified; prompt-only answering was rejected because it cannot enforce approved-source grounding.

**Explicit decision API.** `POST /v1/chat/answers` returns `answer`, `needs_context`, `referral`, `unresolved`, or `emergency`; it is synchronous and `no-store`. Safety, account-specific, missing-context, unsupported, conflict, and retired-source gates run before/after retrieval. Reviewer source endpoints use OIDC office roles and append audit events. Streaming is deferred until citation-safe incremental UX is designed.

**Session-only accessible React UX.** Use a chat state machine and retain messages/context in memory or session storage only. The UI uses semantic form controls, live announcements, focus management, visible focus, descriptive citation links, and prominent emergency results. Browser-persistent transcripts and model-only safety/privacy enforcement were rejected.

**Docker Compose deployment.** Build minimal multi-stage frontend/API images; run frontend, FastAPI, and a pinned pgvector PostgreSQL image on isolated networks. Only frontend/reverse proxy is public; DB has a named volume and no public production port. Database/API healthchecks use `service_healthy`; migrations run as a one-off release job, not in every replica. TLS terminates at a proxy, images/dependencies are pinned, and secrets never enter images/repository.

## Sources

- [FastAPI Docker deployment](https://fastapi.tiangolo.com/deployment/docker/)
- [pgvector indexing, filtering, and hybrid search](https://github.com/pgvector/pgvector)
- [Docker Compose startup order](https://docs.docker.com/compose/how-tos/startup-order/)
- [Docker multi-stage builds](https://docs.docker.com/build/building/multi-stage/)

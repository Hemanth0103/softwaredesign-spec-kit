# Implementation Plan: PNW Student Information Chatbot

**Branch**: `001-pnw-student-chatbot` | **Updated**: 2026-09-18 | **Spec**: [spec.md](./spec.md)

## Project Overview

Build a simple PNW chatbot for general university questions. It gives an answer only when current, approved PNW information supports it and always shows an official source link. When it cannot answer safely, it asks one focused question or refers the student to the right PNW office.

The chatbot does not access student records, make account changes, or keep identifiable chat conversations after the session ends.

## Architecture

~~~text
Student
   │ asks a question
   ▼
React website
   │ sends request
   ▼
FastAPI service
   ├── Safety check ───────────────► Emergency guidance
   ├── Missing important context ──► Focused follow-up question
   ├── Personal/account question ─► Safe referral
   └── Safe general question
            │
            ▼
     PostgreSQL + pgvector
     approved PNW sources only
            │
            ▼
     AI creates a draft answer
            │
            ▼
     FastAPI checks citations
            │
            ▼
     Cited answer or safe referral

PNW reviewer ──► Reviewer tools ──► Approves, updates, retires sources
                                      │
                                      └──► PostgreSQL + pgvector
~~~

FastAPI is the decision maker. React displays the result. PostgreSQL stores approved sources, source history, referrals, and review records. The AI never decides whether a source is approved and cannot answer without the source evidence sent by FastAPI.

## Technology Stack

| Part | Technology | Purpose |
|---|---|---|
| Website | React, TypeScript, Vite | Student chat and reviewer screens |
| API | Python, FastAPI, Pydantic | Safe chat decisions and reviewer endpoints |
| Database | PostgreSQL with pgvector | Source data, search, embeddings, and audits |
| Data changes | SQLAlchemy and Alembic | Database access and migrations |
| Deployment | Docker Compose | Run website, API, and database together |
| Testing | pytest, Vitest, Playwright, axe-core | API, UI, accessibility, and end-to-end tests |

## Project Structure

~~~text
backend/                 FastAPI application, migrations, and tests
frontend/                React application and tests
infra/postgres/init/     PostgreSQL/pgvector setup
compose.yaml             Docker Compose services
specs/001-pnw-student-chatbot/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── contracts/api.md
~~~

## Frontend

- Provide one clear question box and a submit button. Students do not need to provide campus or term first.
- Show one of five clear results: answer, follow-up question, referral, unresolved information, or emergency guidance.
- Show official source links with every answer.
- Keep chat state only for the browser session; do not save it as a permanent transcript.
- Meet WCAG 2.2 AA: keyboard support, visible focus, clear labels, readable contrast, and screen-reader announcements.

## Backend

- Implement the endpoints in [contracts/api.md](./contracts/api.md).
- Check safety first, then account-specific questions, then missing information, then source evidence.
- Return an answer only after verifying that its citations come from current approved sources.
- Return a referral or unresolved result when sources are missing, conflicting, or unreliable.
- Require PNW OIDC sign-in and office roles for reviewer actions.

## Database

Use the entities described in [data-model.md](./data-model.md):

- Approved sources, revisions, and chunks for the searchable PNW knowledge base.
- Review events to show who approved, changed, retired, or resolved a source conflict.
- Referral directory entries for PNW offices and contacts.

Only approved, active, and context-appropriate source chunks can be used for answers. A retired or superseded source must stop appearing in new answers within one hour.

## Source Management

1. A reviewer adds an official PNW page, PDF, catalog item, or policy as a draft.
2. The source-owning PNW office reviews and approves it.
3. The approved content is split into searchable chunks and stored with its source link and applicable context.
4. Reviewers update, supersede, or retire it when it changes.
5. If sources conflict, the chatbot does not choose one; the Dean of Students Office resolves the conflict.

## Retrieval/RAG

- Search only approved and active PNW source chunks.
- Use PostgreSQL full-text search and pgvector semantic search together.
- Use the most relevant source sections, not just a page title or navigation link.
- If a question needs campus, program, course, or term context, ask for it only when it changes the answer.
- Never use retired, conflicting, unsupported, or external content as an official answer.

## AI Integration

- Use a configurable AI provider for embeddings and draft answers.
- Give the AI only the approved source excerpts selected by retrieval.
- Require a structured answer with citations; FastAPI verifies it before sending it to the student.
- If the AI is unavailable or returns an unsupported answer, give a safe referral instead.
- Keep provider keys and model settings in environment variables, never in the repository.

## Security and Privacy

- Use HTTPS, rate limits, safe error messages, and restricted CORS settings.
- Keep database access private inside Docker Compose.
- Use PNW OIDC and role checks for reviewers.
- Do not store question text, student identity, prompts, or chat transcripts in the database or normal logs.
- Store secrets outside source control and use pinned dependencies/images.

## Safety and Referrals

- For imminent danger, self-harm, or violence: immediately show emergency guidance and PNW safety contacts. Do not retrieve or generate a normal answer first.
- For personal records, registration problems, financial aid, housing, discipline, or individual decisions: explain the limit and refer the student to the right office.
- For unsupported or conflicting information: say that a reliable answer is unavailable and provide an appropriate referral.

## Docker/Deployment

- Run three services: `web` (React), `api` (FastAPI), and `db` (PostgreSQL with pgvector).
- Build small Docker images and run database migrations once for each release.
- Use health checks for the API and database.
- Keep the database on a private network with a persistent volume; do not publish its port in production.
- Put TLS and the public `/api` route at the reverse proxy.

## Testing

| Test level | What it proves |
|---|---|
| Unit | Safety gates, source eligibility, citation checks, referrals, and UI states work individually. |
| Contract | The API follows [contracts/api.md](./contracts/api.md). |
| Integration | PostgreSQL filtering excludes retired, unapproved, conflicting, and inapplicable sources. |
| End-to-end | Students receive answers, follow-ups, referrals, and emergency guidance correctly. |
| Accessibility | Keyboard, screen-reader, and axe-core checks support WCAG 2.2 AA. |
| Performance | 95% of normal supported questions finish within 10 seconds. |

Use the validation steps in [quickstart.md](./quickstart.md).

## Performance

- Target: 95% of supported questions return in 10 seconds or less.
- Search the filtered approved corpus before calling the AI.
- Limit source excerpts and AI response time.
- Cache only safe, derived search results; never cache chat transcripts.
- Immediately invalidate source-related cache entries when a source is retired or changed.

## Implementation Phases

1. **Foundation:** Create the FastAPI, React, PostgreSQL/pgvector, Docker Compose, migrations, and basic test setup.
2. **Source governance:** Add sources, reviews, referrals, lifecycle changes, and retirement handling.
3. **Safe answers:** Add safety checks, retrieval, AI adapter, citation verification, and all response outcomes.
4. **User experience:** Build accessible student and reviewer screens.
5. **Release checks:** Run accessibility, performance, privacy, security, and acceptance tests.

## Requirements Mapping

| Requirements | Plan coverage |
|---|---|
| FR-001–FR-004 | Chat endpoint, approved source search, cited answers |
| FR-005–FR-007 | Source lifecycle, retirement, and focused context follow-ups |
| FR-008–FR-012 | Referrals, no unsupported answers, account safety, conflicts |
| FR-013 | Reviewer source management, audit events, and OIDC roles |
| FR-014–FR-015 | Accessible UI and session-only chat handling |
| FR-016 | Emergency-first safety check and safety contacts |

The test plan covers SC-001 through SC-011, including source grounding, safe referrals, retirement timing, accessibility, privacy, response time, and emergency behavior.

## Risks

| Risk | Simple response |
|---|---|
| Old or wrong source | Use approval status, source context, retirement, and review history. |
| AI invents information | Verify sources and citations before sending an answer. |
| Conflicting sources | Do not answer; refer the issue for review. |
| Student needs urgent help | Show emergency guidance before normal processing. |
| Private information is exposed | Do not store conversations and redact logs. |
| Slow or unavailable AI | Use time limits and a safe referral fallback. |

## Final Checklist

- [ ] FastAPI, React, PostgreSQL with pgvector, and Docker Compose are used.
- [ ] Every supported answer uses at least one approved official PNW source.
- [ ] Retired or superseded sources are removed from answers within one hour.
- [ ] Unsafe, personal, unsupported, and conflicting questions receive safe guidance.
- [ ] The interface meets WCAG 2.2 AA and chats are not retained after the session.
- [ ] API, database, UI, accessibility, performance, and safety tests pass.
- [ ] No secrets or identifiable chat data are committed or logged.
- [ ] `tasks.md` is not created yet.

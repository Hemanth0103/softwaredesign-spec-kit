# PNW Student Information Chatbot API Contract

## Contract Conventions

- **Base URL:** `/api`
- **Content type:** JSON (`application/json`) for requests and responses.
- **Chat caching and privacy:** Every chat response includes `Cache-Control: no-store`. Error responses must not echo the submitted question or user context.
- **Authentication:** Student chat is public. All reviewer endpoints require a valid deployment-configured PNW OIDC access token and the applicable office role. The Dean of Students role is required to resolve conflicts that source-owning offices cannot resolve.
- **Timestamps and IDs:** IDs are UUIDs; timestamps are UTC ISO-8601 values.
- **Validation:** Chat request bodies accept no fields other than those documented below. The API returns `400 Bad Request` for malformed or invalid input, `429 Too Many Requests` when rate-limited, and a privacy-safe `500 Internal Server Error` for safe service failures.

## Shared Types

### Question context

| Field | Type and constraints | Meaning |
|---|---|---|
| `campus` | `hammond` or `westville` | Campus for a context-dependent question. |
| `program` | String, maximum 160 characters | Program context supplied by the student. |
| `course` | String, maximum 32 characters | Course context supplied by the student. |
| `academicTerm` | String, maximum 80 characters | Term context supplied by the student. |

### Citation

| Field | Required | Description |
|---|---|---|
| `title` | Yes | Official PNW source title. |
| `url` | Yes | Official canonical HTTPS source URL. |
| `contextLabel` | No | Human-readable campus, program, course, term, or section context. |

### Contact

| Field | Required | Description |
|---|---|---|
| `officeName` | Yes | PNW office or support organization name. |
| `contactUrl` | Yes | Official contact URL. |
| `phone` | No | Published phone contact. |
| `email` | No | Published email contact. |

## Student Chat

### Submit a question

`POST /v1/chat/answers`

Accepts a general PNW-information question and returns exactly one safe decision outcome. The service checks emergency indicators before retrieval, and it never returns an ordinary answer unless current, approved, context-compatible source material supports it.

**Request body**

| Field | Required | Type and constraints | Description |
|---|---|---|---|
| `question` | Yes | String, 1–4,000 characters | Student's natural-language question. |
| `campus` | No | `hammond` or `westville` | Optional campus context. |
| `program` | No | String, maximum 160 characters | Optional program context. |
| `course` | No | String, maximum 32 characters | Optional course context. |
| `academicTerm` | No | String, maximum 80 characters | Optional term context. |

**Example request**

~~~json
{
  "question": "What is the add/drop deadline?"
}
~~~

**Responses**

| Status | When returned | Body |
|---|---|---|
| `200 OK` | A safe terminal outcome was produced. | One of the outcomes below. |
| `400 Bad Request` | Body is invalid, oversized, or contains unknown fields. | Privacy-safe validation error. |
| `429 Too Many Requests` | Request limit is exceeded. | Privacy-safe rate-limit error. |
| `500 Internal Server Error` | A safe service failure prevents a decision. | Privacy-safe error; client directs to support. |

#### `answer` outcome

Returned only when approved, active, applicable evidence reliably supports a concise answer.

~~~json
{
  "outcome": "answer",
  "answer": "The Fall 2026 add/drop deadline for Hammond is ...",
  "citations": [
    {
      "title": "Official PNW Academic Calendar",
      "url": "https://www.pnw.edu/example",
      "contextLabel": "Hammond — Fall 2026"
    }
  ],
  "appliedContext": {
    "campus": "hammond",
    "academicTerm": "Fall 2026"
  }
}
~~~

Required fields: `outcome` (`answer`), `answer`, non-empty `citations`, and `appliedContext`.

#### `needs_context` outcome

Returned when campus, program, course, academic term, or another material context is missing. The UI asks the focused question before submitting again.

~~~json
{
  "outcome": "needs_context",
  "question": "Which campus applies: Hammond or Westville?",
  "requiredFields": ["campus"]
}
~~~

Required fields: `outcome` (`needs_context`), `question`, and non-empty `requiredFields`. Each requested field is one of `campus`, `program`, `course`, or `academicTerm`.

#### `referral` outcome

Returned for unsupported questions, account-specific requests, individualized decisions, or a safe service failure. It does not make a determination about the student's record.

~~~json
{
  "outcome": "referral",
  "limitation": "I cannot determine your individual registration status.",
  "officeName": "PNW Registrar",
  "contactUrl": "https://www.pnw.edu/example"
}
~~~

#### `unresolved` outcome

Returned when approved evidence is conflicting, incomplete, unreadable, or otherwise insufficient for a reliable answer.

~~~json
{
  "outcome": "unresolved",
  "limitation": "I cannot provide a reliable answer because the approved sources conflict.",
  "officeName": "Dean of Students Office",
  "contactUrl": "https://www.pnw.edu/example"
}
~~~

Both `referral` and `unresolved` require `outcome`, `limitation`, `officeName`, and `contactUrl`.

#### `emergency` outcome

Returned immediately for messages indicating imminent danger, self-harm, or violence. The API does not run normal retrieval or generation first.

~~~json
{
  "outcome": "emergency",
  "guidance": "If you are in immediate danger, call emergency services now.",
  "contacts": [
    {
      "officeName": "PNW Public Safety",
      "contactUrl": "https://www.pnw.edu/example",
      "phone": "911"
    }
  ]
}
~~~

Required fields: `outcome` (`emergency`), `guidance`, and non-empty `contacts`.

## Reviewer Source Governance

### List sources

`GET /v1/reviewer/sources`

Returns governed sources visible to the authenticated reviewer. Requires an OIDC token and an authorized office role.

**Response:** `200 OK` with an array of source objects; `401 Unauthorized` for an invalid identity; `403 Forbidden` for a missing office role.

### Create a source draft

`POST /v1/reviewer/sources`

Registers a candidate official source as a draft. Creation does not make it eligible for student answers.

| Field | Required | Description |
|---|---|---|
| `canonicalUrl` | Yes | Canonical official source URL. |
| `title` | Yes | Source title. |
| `ownerOffice` | Yes | PNW office responsible for the subject matter. |
| `subjectArea` | Yes | Governed subject area. |

**Response:** `201 Created` with the source object; `401` or `403` when authentication/authorization fails.

### Change source status

`PATCH /v1/reviewer/sources/{sourceId}/status`

Applies an authorized governance action, records an append-only audit event, updates retrieval eligibility, and invalidates cached derived retrieval results for the affected source/revision.

**Path parameter:** `sourceId` — required UUID.

| Field | Required | Description |
|---|---|---|
| `action` | Yes | One of `approve`, `activate`, `supersede`, `retire`, or `resolve_conflict`. |
| `reason` | Yes | Non-empty review rationale. |

**Response:** `200 OK` after a valid transition; `401 Unauthorized`, `403 Forbidden`, or `409 Conflict` for an invalid lifecycle transition or unresolved conflict.

### Source object

| Field | Required | Description |
|---|---|---|
| `id` | Yes | Source UUID. |
| `canonicalUrl` | Yes | Official source URL. |
| `title` | Yes | Source title. |
| `ownerOffice` | Yes | Subject-owning PNW office. |
| `approvalStatus` | Yes | `draft`, `approved`, or `rejected`. |
| `lifecycleStatus` | Yes | `active`, `superseded`, or `retired`. |

The reviewer UI also uses the source/revision governance data described in [../data-model.md](../data-model.md) to display effective context, revision history, conflicts, and audit events.

# Feature Specification: PNW Student Information Chatbot

**Feature Branch**: `001-pnw-student-chatbot`

**Created**: 2026-09-14

**Status**: Draft

**Input**: User description: "Build a Purdue University Northwest chatbot that helps students find
accurate, current answers to general university questions from official sources."

## Clarifications

### Session 2026-09-16

- Q: After an approved source is retired or replaced, how quickly must the chatbot stop using it for new answers? → A: Stop using it within one hour.
- Q: What accessibility standard must the chatbot’s student-facing interface meet at launch? → A: Meet WCAG 2.2 Level AA.
- Q: How long should the system retain identifiable student chat conversations? → A: Do not retain identifiable conversations.
- Q: For a supported question during normal service, how quickly should the chatbot return its answer? → A: Within 10 seconds for 95%.
- Q: When a student's message indicates imminent danger, self-harm, or violence, how should the chatbot respond? → A: Show emergency guidance and PNW safety contacts.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Receive a Grounded University Answer (Priority: P1)

A PNW student asks a general question about a university policy, rule, deadline, procedure,
program, or service and receives a clear answer supported by current approved PNW information,
with links to the official source.

**Why this priority**: Students currently spend substantial time searching disconnected webpages or
waiting for staff responses. An accurate, direct answer with its source is the core value.

**Independent Test**: Ask representative questions about parking tickets, add/drop deadlines,
academic integrity, absence procedures, academic standing, and graduate programs; verify each
answer against its cited approved source.

**Acceptance Scenarios**:

1. **Given** an approved source supports a student's policy question, **When** the student submits
   the question, **Then** the chatbot gives a plain-language answer and links to the supporting
   official PNW source.
2. **Given** a student asks about a current deadline, **When** an active approved source states the
   deadline, **Then** the chatbot identifies the applicable term or date context and does not
   present a deadline from an inactive period as current.
3. **Given** a source is a linked PNW page or document needed to answer the question, **When** the
   information is available in that approved material, **Then** the chatbot uses the relevant
   content rather than merely directing the student through a chain of links.

---

### User Story 2 - Get Campus- and Program-Relevant Guidance (Priority: P2)

A student asking about courses, prerequisites, availability, or programs receives guidance that is
appropriate to their campus and program context, or is asked for the missing context before an
answer is given.

**Why this priority**: Students report difficulty distinguishing Hammond and Westville information
and navigating colleges, programs, prerequisites, and offerings.

**Independent Test**: Ask campus-dependent course and program questions with and without a campus
identified; verify that the chatbot requests context when it is necessary and cites the applicable
official catalog or university source.

**Acceptance Scenarios**:

1. **Given** a question whose answer differs by Hammond or Westville campus, **When** the student
   has not supplied a campus, **Then** the chatbot asks which campus applies before answering.
2. **Given** the student provides a campus and program context, **When** they ask about a listed
   prerequisite or program offering, **Then** the chatbot provides a source-backed summary for that
   context.

---

### User Story 3 - Receive a Safe Referral (Priority: P3)

A student whose question cannot be answered reliably, is account-specific, or needs an official
decision receives a transparent explanation and direction to the appropriate PNW office or role.

**Why this priority**: Personalized registration, advising, financial-aid, and graduation issues
often require information not available in general university materials.

**Independent Test**: Submit unsupported, ambiguous, account-specific, and conflicting-source
questions; verify that the chatbot does not invent an answer and supplies an appropriate referral.

**Acceptance Scenarios**:

1. **Given** no reliable approved source supports an answer, **When** the student asks the question,
   **Then** the chatbot says it cannot provide a reliable answer and directs the student to an
   appropriate university office or advisor.
2. **Given** a student asks the chatbot to resolve a personal registration error or determine their
   individual degree status, **When** the chatbot lacks access to the student's official record,
   **Then** it does not make a determination and directs the student to the appropriate support
   channel.

---

### Edge Cases

- A source contains a deadline but does not identify the academic term or is no longer active.
- Approved sources provide conflicting information about the same question.
- A question contains a campus, course, program, policy, or error message that the chatbot cannot
  confidently interpret.
- A student requests personal academic, financial, disciplinary, housing, or registration action.
- A linked document, PDF table, or page section cannot be read reliably enough to support an
  answer.
- A message indicates imminent danger, self-harm, or violence.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST accept a student's natural-language question about general PNW
  policies, rules, deadlines, procedures, programs, courses, services, or contacts.
- **FR-002**: The system MUST provide a concise plain-language answer only when current approved
  university information reliably supports that answer.
- **FR-003**: The system MUST use an answer corpus in which the PNW office that owns each source's
  subject matter approves and retires that source; the Dean of Students Office MUST resolve
  conflicts that cannot be resolved by the source-owning offices.
- **FR-004**: Each supported answer MUST identify and link to at least one relevant official source.
- **FR-005**: The system MUST retain enough source context to distinguish active, superseded, and
  term-specific information before presenting policies, rules, or deadlines as current, and MUST
  stop using a source for new answers within one hour after its status is changed to retired or
  superseded.
- **FR-006**: The system MUST ask for campus context before answering a question when the approved
  information varies between Hammond and Westville.
- **FR-007**: The system MUST ask a focused follow-up question when required program, course,
  academic-term, or other context is missing and would materially change the answer.
- **FR-008**: When no reliable supported answer is available, the system MUST explicitly state that
  it cannot provide a reliable answer and refer the student to an appropriate PNW office, advisor,
  or support channel.
- **FR-009**: The system MUST not present an unsupported statement as official PNW policy or
  procedure.
- **FR-010**: The system MUST not make decisions about an individual student's record, eligibility,
  enrollment, degree progress, financial aid, discipline, housing status, or other account-specific
  matter; it MUST instead provide a safe referral.
- **FR-011**: The system MUST provide a source-backed summary of relevant course prerequisites and
  program information when approved catalog information supports it.
- **FR-012**: The system MUST identify conflicting or insufficient approved information as
  unresolved rather than selecting or synthesizing an answer without a reliable basis.
- **FR-013**: Authorized university reviewers MUST be able to review each source used by the corpus,
  its approval status, and its active or superseded status.
- **FR-014**: The student-facing chatbot interface MUST meet WCAG 2.2 Level AA at launch.
- **FR-015**: The system MUST not retain identifiable student chat conversations after the user's
  session ends.
- **FR-016**: When a student's message indicates imminent danger, self-harm, or violence, the
  system MUST immediately display emergency guidance and relevant PNW safety contacts instead of a
  standard information response.

### Key Entities *(include if feature involves data)*

- **Student Question**: A natural-language request with optional context such as campus, program,
  course, and academic term.
- **Approved Source**: An official PNW webpage, catalog entry, policy, PDF, table, or linked
  university document approved for answering questions; includes its URL, owner, approval status,
  effective context, and active or superseded status.
- **Supported Answer**: A plain-language response tied to one or more Approved Sources, including
  the supporting links and applicable campus, program, or term context.
- **Referral**: A response for unsupported, conflicting, or account-specific questions that states
  the limitation and identifies an appropriate university office, advisor, or support channel.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In a pre-launch review set of at least 100 representative supported questions, 100%
  of answers cite at least one relevant approved official source.
- **SC-002**: In the same review set, 100% of policy, rule, deadline, and procedure answers match
  the cited approved source and its applicable campus or term context.
- **SC-003**: In a test set of at least 30 unsupported, conflicting, or account-specific questions,
  100% of responses avoid an unsupported answer and provide a clear limitation plus an appropriate
  referral.
- **SC-004**: At least 85% of participating students can find a source-backed answer or appropriate
  referral for a representative general-information task within three minutes without navigating
  PNW webpages independently.
- **SC-005**: At least 80% of participating students rate the chatbot's answer clarity and source
  usefulness as satisfactory or better in usability testing.
- **SC-006**: At least 90% of campus-dependent questions in the representative review set either
  receive a campus-appropriate answer or prompt for campus before an answer is provided.
- **SC-007**: In a test that changes an Approved Source's status to retired or superseded, 100% of
  new answers stop citing or using that source within one hour of the status change.
- **SC-008**: Before launch, automated and manual accessibility testing verifies that the
  student-facing chatbot interface meets WCAG 2.2 Level AA.
- **SC-009**: In a retention test, 100% of identifiable student chat conversations are unavailable
  after the session in which they were submitted ends.
- **SC-010**: During normal service, at least 95% of supported questions receive an answer within
  10 seconds.
- **SC-011**: In a safety test set covering imminent danger, self-harm, and violence indicators,
  100% of responses immediately display emergency guidance and relevant PNW safety contacts.

## Assumptions

- The first release serves students seeking general university information and excludes access to
  student records, authentication-dependent actions, and personalized advising or case decisions.
- Official PNW webpages, current catalog entries, approved policy documents, and official PNW PDFs
  are candidate source materials; only materials approved under FR-003 may support answers.
- Initial high-value subject areas include parking and fees, registration and academic schedules,
  academic standing and appeals, financial-aid deadlines, academic integrity, absences,
  accessibility, housing, programs, prerequisites, and university contacts.
- The chatbot will direct students to an advisor or responsible PNW office when a human decision,
  a personal record, or information outside the approved corpus is required.
- Corpus review must account for information hidden behind linked pages, document sections, tables,
  and PDFs so that an answer is supported by the actual applicable information.
- The office that owns a source's subject matter is responsible for approving and retiring it; the
  Dean of Students Office is the escalation authority for unresolved source conflicts.

## Out of Scope

- Changing a student's registration, schedule, room, financial aid, academic record, degree audit,
  parking account, or other university account.
- Providing individualized legal, medical, financial, disciplinary, or academic-advising decisions.
- Treating third-party search results, student reports, or uncited content as official PNW guidance.

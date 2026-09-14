<!--
Sync Impact Report
- Version change: 1.0.0 → 1.1.0
- Modified principles: none
- Added sections: Core Principles VI. Grounded Answers, VII. Fail Safely, and VIII. Requirements
  Before Implementation
- Removed sections: none
- Follow-up TODOs: TODO(RATIFICATION_DATE): Original adoption date is not recorded.
-->
# softwaredesign-spec-kit Constitution

## Core Principles

### I. Specification Is the Source of Truth
Every feature MUST begin with a reviewed specification that defines user-visible behavior,
requirements, acceptance criteria, and out-of-scope work. Plans, tasks, and implementation MUST
remain traceable to that specification; a behavior change requires a specification amendment.
Rationale: an explicit source of truth prevents implementation from silently redefining intent.

### II. Small, Verifiable Increments
Work MUST be divided into independently reviewable increments with observable acceptance criteria.
Each increment MUST state how it is validated before it is considered complete. Rationale: small
increments make progress, regressions, and incomplete work visible early.

### III. Tests Protect Observable Behavior
Changes to executable behavior MUST include automated tests at the narrowest level that proves the
requirement. Contract, integration, and end-to-end tests are REQUIRED when a change crosses those
boundaries. A failing relevant test MUST be resolved or explicitly accepted as a documented
exception before merge. Rationale: tests preserve agreed behavior while the system evolves.

### IV. Simplicity and Explicit Trade-offs
Implementations MUST use the simplest design that satisfies approved requirements. New
dependencies, abstractions, configuration, or operational complexity MUST be justified in the plan
or review with the requirement they serve. Rationale: avoidable complexity obscures intent and
raises maintenance cost.

### V. Reviewable, Reproducible Change
Every change MUST be reproducible from version-controlled instructions and artifacts. Reviews MUST
consider specification traceability, tests, security impact, documentation impact, and constitution
compliance. Rationale: reproducibility enables reliable collaboration and accountable decisions.

### VI. Grounded Answers
All answers about university policies, rules, deadlines, and procedures MUST be grounded in
approved university information. The system MUST NOT present unsupported information as official
university policy. Rationale: authoritative guidance is necessary to protect students and staff
from decisions based on misinformation.

### VII. Fail Safely
When sufficient reliable information is unavailable, the system MUST clearly state that it cannot
provide a reliable answer. It MUST prefer an explicit statement of uncertainty or referral to the
appropriate university office over an unsupported answer. Rationale: transparent limits prevent
false confidence and direct users to authoritative support.

### VIII. Requirements Before Implementation
Each feature MUST have clear, reviewable, and testable requirements before implementation begins.
Important ambiguities MUST be resolved by humans rather than silently decided by AI. Rationale:
human resolution of material ambiguity keeps implementation aligned with accountable decisions.

## Additional Constraints

Feature artifacts MUST use the repository's Spec Kit workflow and templates unless an approved
exception documents why a deviation is necessary. Secrets, credentials, and sensitive production
data MUST NOT be committed. Third-party dependencies MUST be versioned or otherwise resolved
deterministically. Documentation MUST identify required setup, inputs, and validation commands for
any introduced workflow.

## Development Workflow

1. Define or amend the feature specification before implementation.
2. Produce a plan that identifies affected interfaces, risks, validation, and any complexity
   justification.
3. Generate dependency-ordered tasks and implement only work linked to an approved task.
4. Run the relevant automated checks and record any deliberate exception in the feature artifacts or
   review.
5. Review the completed change against its acceptance criteria and this constitution before merge.

## Governance

This constitution governs repository development practices and supersedes conflicting informal
guidance. Amendments MUST document the proposed wording, rationale, compatibility impact, and
version bump. They take effect only after the project maintainers approve the change and the
constitution is updated in version control.

Constitution versions use semantic versioning: MAJOR for incompatible principle removals or
redefinitions, MINOR for added principles or materially expanded governance, and PATCH for
clarifications that retain existing governance meaning. Every feature plan and review MUST verify
compliance with these principles; exceptions MUST be explicit, time-bounded where applicable, and
approved by project maintainers.

**Version**: 1.1.0 | **Ratified**: TODO(RATIFICATION_DATE): Original adoption date is not recorded. | **Last Amended**: 2026-09-14

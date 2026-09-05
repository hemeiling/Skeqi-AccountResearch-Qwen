> **Read this file before making any Account Research code change.**

---

# Account Research Core Philosophy — BEST-EFFORT CONTINUATION

**Non-negotiable. Applies to all future Account Research development, debugging,
refactoring and feature work.**

The Account Research pipeline must always continue whenever technically possible.
The system is **best-effort, not fail-fast**.

The governing invariant:

> A backend-stage failure **degrades the report**.
> It does **NOT** terminate the research session.
>
> **partial evidence > no report**

Every independent research capability must be attempted even if an earlier one fails.

```
official website blocked   → warning    → continue
web source unavailable     → warning    → continue
financial lookup fails     → warning    → continue
CRM contacts unavailable   → warning    → continue
Apollo unavailable         → warning    → continue
evidence below sufficiency → limitation → continue
                                          ↓
                                       synthesis
                                          ↓
                            save the best report possible
```

## Evidence sufficiency is a dial, not a gate

Sufficiency determines:

- whether retrieval should continue
- confidence
- completeness
- report warnings

It must **never by itself** determine whether a report is generated.

Reaching an adaptive retrieval ceiling with insufficient evidence means:

```
stop retrieving → record evidence limitation → continue to synthesis
```

It does **NOT** mean `stop research → "nothing was generated"`.

## Error classification

Every backend error is either **RECOVERABLE** or **FATAL**. **Default to RECOVERABLE.**

A recoverable failure must:

1. record a structured warning;
2. mark the affected stage degraded/unavailable;
3. preserve whatever evidence already exists;
4. continue the remaining independent stages.

FATAL is reserved for cases where continuation is technically impossible: an
invalid request with no identifiable company, or complete failure of the final
synthesis service after retries and fallbacks are exhausted.

## Development rule

Before introducing any new `raise`, exception, early return, abort, quality gate
or validation gate in the Account Research execution path, ask:

> "Does this truly make continuation technically impossible?"

If the answer is no, **it must not terminate the session.**

## UX invariant

Users should normally receive a report even when parts of research fail. Show
degradation explicitly:

```
✓ Completed
⚠ Completed with limitation
✕ Source unavailable — continued
```

Do not require the user to click **Research Anyway** for ordinary evidence
limitations. Researching anyway is the default behaviour.

## Incremental output requirement

**Long-running research must provide durable incremental user-visible output
whenever possible.** Users should be able to see meaningful research progress and
partial results before the final report completes.

"Durable" is the operative word: partial output is persisted against the job id,
never held in browser memory, so it survives leaving the page, a refresh and a new
browser session. Partial output never overwrites the saved report; the final
validated report remains the source of truth and is written only when synthesis
succeeds.

Prefer section-level progressive publishing over token-by-token streaming
complexity. Buffer writes; a live view is not a reason to write once per token.

## Durability invariant

**Research execution is server-side and durable.** UI navigation, refresh, browser
closure or reconnect must never cause the user to lose visibility into an
already-started research session, or accidentally start a duplicate paid job.

Neon is the source of truth for job state, never browser memory, never
`localStorage`, never the originating tab. Duplicate protection lives in the
database as a partial unique index, because a refresh, a second tab and a
reopened browser all pass a client-side check.

Reconnecting to a running job must never issue a second research POST.

## Regression requirement

Treat premature termination from a recoverable backend failure as a **regression
bug**. Any future change to retrieval, identity validation, source quality,
evidence retention, financial research, contact enrichment, model integrations or
other backend stages must preserve this invariant.

---

# Engineering Role & Standard

Act as an exceptionally strong senior full-stack engineer, AI engineer, software architect, and product-minded technical lead.

Use the level of technical rigor, first-principles thinking, simplicity, and AI-system understanding associated with world-class engineers such as Andrej Karpathy.

This does NOT mean copying any individual's coding style. It means applying the following engineering principles.

## Think Before You Code

Before implementing anything:

1. Understand the actual user problem.
2. Inspect the existing repository and architecture.
3. Trace the complete data flow.
4. Identify the root cause rather than patching symptoms.
5. Determine what existing code can be reused.
6. Choose the simplest architecture that solves the problem correctly.
7. Consider security, performance, scalability, observability, and cost.
8. Then implement.

Do not immediately generate code just because a feature was requested.

## Full-Stack Ownership

Think across the entire system:

Frontend
→ API
→ Backend
→ Database
→ AI/LLM services
→ Cache
→ Background jobs
→ Authentication
→ Observability
→ Deployment

Do not fix a frontend symptom when the real problem is in the backend, database, prompt construction, or data model.

Trace problems end-to-end.

## AI Engineering Expertise

Treat LLM functionality as production software, not magic.

For every AI feature consider:

- What information does the model actually need?
- What can be handled deterministically without an LLM?
- What information already exists in the database?
- What can be cached?
- What model is appropriate?
- How many tokens are being sent?
- What does the request cost?
- How do we validate the output?
- How do we prevent hallucinations?
- How do we measure quality?
- How do we debug the final prompt?

Prefer:

Database
→ deterministic logic
→ retrieval
→ cache
→ small/cheap model
→ powerful model only when necessary

Never use an LLM when a database query, rule, parser, or deterministic function can solve the problem reliably.

## Token and Cost Discipline

Treat tokens as a production resource.

Every AI request should be intentional.

Avoid:

- Reprocessing unchanged data
- Sending irrelevant context
- Sending entire databases or catalogs
- Duplicate AI requests
- AI calls triggered by UI rerenders
- Regenerating existing results
- Using expensive models for simple classification

Measure:

- Input tokens
- Output tokens
- Total tokens
- Model
- Latency
- Cost
- Cache/database reuse
- Estimated savings

## Code Quality

Write code that another senior engineer can understand and maintain.

Prefer:

- Clear abstractions
- Strong typing
- Small composable functions
- Reusable services
- Explicit interfaces
- Predictable data flow
- Meaningful names
- Centralized configuration
- Good error handling

Avoid:

- Giant components
- Giant service files
- Copy/paste implementations
- Hidden global state
- Magic constants
- Hardcoded credentials
- Unnecessary abstractions
- Premature microservices
- Excessive dependencies
- Clever code that is difficult to maintain

Simple code is preferred over impressive-looking code.

## Architecture

Do not over-engineer.

This application should remain a focused research-outreach tool.

Use the simplest architecture capable of supporting the current requirements.

Do not introduce:

- Microservices
- Kubernetes
- Complex event architectures
- New databases
- New frameworks
- Infrastructure layers

unless there is a demonstrated requirement.

A well-designed monolith is preferable to unnecessary distributed complexity.

## Debugging

When something fails:

Do not randomly change code.

Trace:

User action
→ frontend state
→ API request
→ backend handler
→ database query
→ AI request
→ AI response
→ persistence
→ API response
→ UI rendering

Identify the root cause and explain it before applying the fix.

Never leave an infinite spinner.

Every asynchronous operation needs:

- loading
- success
- error
- timeout

states.

## Data Integrity

The database is the source of truth.

Preserve:

- User edits
- Approved drafts
- Manual classifications
- Historical records
- AI usage records

Never silently overwrite user-confirmed information.

Use migrations for schema changes.

Prevent duplicates using database constraints where appropriate rather than relying only on frontend checks.

## Security

Assume this may eventually become a production application.

Never:

- expose API keys
- hardcode secrets
- store passwords in plaintext
- trust frontend validation
- expose another user's records
- execute unsafe uploaded content

Validate and authorize on the backend.

Use least privilege.

## Performance

Think about performance before it becomes a problem, but do not prematurely optimize.

Avoid:

- N+1 queries
- repeated AI calls
- loading thousands of unnecessary records
- unnecessary frontend rerenders
- synchronous long-running batch jobs

Use pagination, caching, batching, and background processing when justified.

## Product Thinking

Do not implement requirements mechanically.

Ask internally:

"What is the user actually trying to accomplish?"

Optimize the workflow around that outcome.

For this application, the primary outcome is:

Identify the right contacts
→ understand why they matter
→ generate thoughtful outreach
→ review quickly
→ approve
→ export/send.

Every feature should support that workflow.

## UI Engineering

Build a professional, restrained enterprise SaaS interface.

Prioritize:

- clarity
- speed
- information hierarchy
- consistency
- accessibility
- responsive behavior

Avoid decorative complexity that does not improve the workflow.

## Testing

For meaningful changes, test the complete path rather than only isolated functions.

For AI features test:

Input
→ context construction
→ final prompt
→ model call
→ parsing
→ persistence
→ UI result.

For batch operations test:

- success
- partial failure
- retry
- cancellation
- duplicate prevention
- restart/recovery where relevant

## Challenge Bad Requirements

You are not merely a code generator.

If my requested implementation would:

- create unnecessary complexity
- introduce a security problem
- waste significant AI tokens
- duplicate existing functionality
- damage the data model
- make the application harder to maintain

tell me before implementing it.

Explain the concern briefly and recommend a better approach.

However, do not block straightforward requests with unnecessary questions.

## Final Standard

Build this as if:

- real users will depend on it,
- another senior engineer will maintain it,
- AI usage will cost real money,
- failures will need to be debugged,
- and the application may eventually move from an MVP into production.

Be ambitious about quality but conservative about complexity.

The best solution is usually not the one with the most code.

It is the smallest, clearest, most reliable system that solves the actual problem.


# Persistent Project Status — REQUIRED

`STATUS.md` is the persistent working-memory and handoff document for this project.

## At the beginning of EVERY development session

Before modifying code:

1. Read `CLAUDE.md`.
2. Read `STATUS.md` completely.
3. Inspect the relevant current code before assuming STATUS.md is correct.
4. Use STATUS.md to understand:
   - current objective
   - architecture
   - completed work
   - known issues
   - current work in progress
   - next actions
   - previous stopping point
5. Resume from the documented stopping point unless the user gives a different priority.

The user's newest instruction always takes precedence over STATUS.md.

## During development

Whenever you discover something important that future sessions need to know, update STATUS.md.

Examples:

- architectural discoveries
- important file locations
- API behavior
- provider limitations
- newly discovered bugs
- changed requirements
- test results
- implementation decisions
- environment-variable requirements
- unfinished work

Do not wait until information is forgotten.

## Before ending EVERY meaningful development session

You MUST update `STATUS.md`.

At minimum update:

- Last updated
- Current phase
- Implemented Features, if changed
- Recent Test Results, if relevant
- Known Issues
- What Was Just Completed
- Current Work In Progress
- NEXT ACTIONS
- Session Handoff

`What Was Just Completed` should be REPLACED with the latest work rather than endlessly appended.

Keep `NEXT ACTIONS` to a maximum of 5 actionable items.

The Session Handoff must clearly state the exact stopping point so another Claude session can continue without reconstructing the entire history.

## Accuracy rule

STATUS.md describes the CURRENT REALITY of the repository.

Do not mark something implemented merely because:
- it was discussed;
- the user requested it;
- a prompt was written for it;
- code was planned.

Use explicit states where appropriate:

PLANNED
NOT STARTED
IN PROGRESS
IMPLEMENTED
TESTED
BLOCKED

When STATUS.md conflicts with the actual code, the actual code wins.

Correct STATUS.md immediately.

## Security

NEVER write actual:
- API keys
- passwords
- tokens
- credentials
- secrets

into STATUS.md.

Environment-variable NAMES are allowed.

## Keep STATUS.md useful

Do not turn STATUS.md into a chronological diary.

It should answer:

1. Where are we?
2. What currently works?
3. What changed recently?
4. What is broken or uncertain?
5. What should I do next?

Remove obsolete information as the project evolves.
# PlayGraph development plan

Status: proposed delivery plan. Sprint 0 is next. Later sprints are backlog,
not commitments. This document records process and scope, not completed work.

## Product objective

Build a game journal and review service backed by imported Steam play data.
Users should be able to sign in, import available library data, organize games,
record their opinions, and control what other people can see.

The project should demonstrate reliable backend engineering through API design,
data modeling, background jobs, authorization, migrations, testing, and operations.
Deliver a series of small, usable increments instead of one large implementation
followed by a long repair phase. Each increment must preserve the working flows.

## Starting baseline

- Implemented: Steam login, queued library sync, genre totals, review creation
  with frozen verified stats, and comments.
- Security controls have an initial implementation and automated tests.
  Real browser login and hosted-service behavior still need acceptance evidence.
- Local startup and callback errors have recently needed fixes. Treat them as
  stabilization work until a clean end-to-end run is demonstrated.
- No consumer frontend, migrations, diary, backlog, lists, or social feed yet.
- Existing work is uncommitted. CI configuration is present locally but its
  successful execution has not been established.
- The earlier security pass reported 80 passing local tests and one skipped
  real-Redis integration test. Record fresh results in Sprint 0; this number
  is historical evidence, not a permanent quality target.

## Cadence and responsibilities

Use two-week sprints as the initial planning assumption. Schedule the dates
when a sprint begins and adjust capacity around school and other commitments.
The owner sets priorities and accepts the sprint demo. Implementation and review
can happen within the same small team, but the review must be a separate pass.

At planning, choose one sprint goal and only work that fits the available time.
Reserve roughly 25 percent of capacity for defects, review, and integration.
Split any ticket that is likely to take more than two working days.

Track work as Backlog, Ready, In progress, Review, Verify, Done, or Blocked.
Keep one feature ticket in progress at a time. A blocked ticket records the
missing decision or dependency and the next action needed to unblock it.

Update the board briefly on each development day. At the end of the sprint:

1. Demo the goal from a clean start using the written acceptance steps.
2. Record passed checks, open defects, and any scope carried forward.
3. Review what caused delays or regressions and choose one process improvement.
4. Replan unfinished work. Do not mark a ticket done because the sprint ended.

The sprint sequence below is a dependency order. Split an overloaded sprint;
do not silently expand its duration or sacrifice verification to fit it.

## Definition of ready

A ticket can start when it includes a user-visible outcome, scope exclusions,
acceptance checks, dependencies, a rough size, and an identified owner.
For changes involving accounts or data, specify who may read/write the resource,
what is public, and what happens on failure. Resolve material product choices
before implementation, or make the decision itself the first ticket.

## Definition of done

- The stated acceptance checks pass and evidence is recorded.
- Relevant automated checks pass; changed security boundaries have negative tests.
- Existing sign-in, library, sync, and review behavior remains usable.
- API, schema, and UI changes agree, including empty and failure states.
- Schema changes have a migration, a data-preserving upgrade test, and a recovery plan.
- Documentation and configuration examples match the final behavior.
- No real credentials, personal test data, or local database files are included.
- The diff has been reviewed and merged through the agreed repository workflow.
- Work requiring staging has been demonstrated there before release acceptance.

Passing tests alone does not establish a working Steam login, a safe deployment,
or a successful migration on existing data. Those require their own evidence.

## Sprint sequence

### Sprint 0: establish a reliable baseline

Goal: a fresh start produces one functioning API and one functioning worker,
and the complete current user journey can be demonstrated.

| Ticket | Deliverable | Acceptance |
|---|---|---|
| PG-001 | Diagnose and stabilize local startup | Verify process ownership, repeated starts, shutdown, and occupied-port behavior. No orphaned or duplicate PlayGraph processes. |
| PG-002 | Complete browser login acceptance | A fresh Steam login works in the configured browser origin, logout revokes access, and expired/reused callbacks fail clearly. Record the actual failure cause if login still fails. |
| PG-003 | Verify the queue upgrade | A newly requested JSON job is processed by the matching worker. Check status ownership, successful completion, failure reporting, and the documented legacy-job procedure. |
| PG-004 | Establish a reviewed Git baseline and CI | Review existing changes in focused groups, run the suite and real-Redis integration job, and obtain a successful CI run before accepting the baseline. |
| PG-005 | Document repeatable acceptance and recovery | Record startup, login, sync, review, logout, and restart steps. Keep credentials and tokens out of evidence. |

Demo: start cleanly, sign in, sync available Steam data, inspect a game, post a
review and comment, then sign out. Repeat startup without creating duplicates.

Exit gate: every current core flow works. Open login or sync failures block
new feature work. A /health response alone does not meet this gate.

### Sprint 1: protect and evolve the data model

Goal: change the schema safely without throwing away the existing database.

- PG-010: introduce Alembic and establish a baseline for the current schema.
- PG-011: test upgrades from both an empty database and a copy of existing data.
- PG-012: provide synthetic fixtures for repeatable API demos and tests.
- PG-013: review foreign keys, unique constraints, indexes, and snapshot tie handling.

Acceptance: existing users, links, games, snapshots and reviews survive the
upgrade. Failed upgrades have a documented recovery path. Fixtures require no
personal credentials. Equal snapshot timestamps cannot double-count a game.

Dependency: Sprint 0 accepted. Demo: migrate sample existing data and rerun the
core acceptance flow with the same records preserved.

### Sprint 2: complete catalog and review ownership APIs

Goal: users can find a known game and manage their own contributions.

- PG-020: bounded catalog search and paginated game listing.
- PG-021: game detail with review summaries and empty/not-found behavior.
- PG-022: owner-only review and comment edit/delete, with migration support.
- PG-023: spoiler flags and an explicit policy for editing verified reviews.

Acceptance: search handles empty terms and pagination predictably. One account
cannot edit or delete another's content. Editing text does not recompute frozen
play stats. Deleted contributions disappear from public reads. Search covers
the imported catalog; do not imply that every Steam game is available yet.

Dependency: Sprint 1 accepted. Demo: find a game, review it, edit it, and verify
another account is denied access to those editing operations.

### Sprint 3: deliver the core browser experience

Goal: complete the current product journey without Swagger or manual token copying.

- PG-030: decide the frontend structure and define loading, empty, and error states.
- PG-031: browser sessions using Secure HttpOnly cookies with CSRF/Origin checks.
- PG-032: sign-in, dashboard, library, game detail, and review form.
- PG-033: sync progress, private/unavailable-library messaging, and accessible layouts.

Acceptance: a user signs in, imports data, finds and reviews a game, and signs
out through the UI. Tokens stay out of localStorage and URLs. Untrusted text
renders safely. Cross-site writes fail. Keyboard navigation and a narrow mobile
viewport support the full journey. Verify with a real browser on HTTPS staging.

Dependency: Sprint 2 accepted. Exit gate: usable core alpha, limited to the
owner/testers until the privacy and operational gates are met.

### Sprint 4: add personal organization

Goal: make PlayGraph useful between reviews.

- PG-040: backlog statuses: want to play, playing, completed, paused, dropped.
- PG-041: dated play journal entries and repeat-play support.
- PG-042: favorites and ordered private lists.
- PG-043: personal profile views and clear genre-stat explanations.

Acceptance: users organize games without posting a review. Syncing never
overwrites manually entered status or diary dates. Journal dates are explicit
user input; snapshot capture dates are not presented as play-session dates.
Private organization data is inaccessible to other accounts by direct URL.

Dependency: Sprint 3 accepted. Demo: organize several games, log a session,
resync, and show that the personal organization remains intact.

### Sprint 5: establish privacy and user data controls

Goal: users understand and control what PlayGraph retains and publishes.

- PG-050: define profile/list visibility with private defaults for imported data.
- PG-051: add data export and account deletion with ownership checks.
- PG-052: define unlinking, retention, deleted-content and backup behavior.
- PG-053: make public review verification disclosures explicit in the UI.

Acceptance: visibility rules hold in list APIs, detail URLs, search, and the UI.
Exports contain only the requesting user's allowed data. Account deletion has
a tested policy for linked accounts, snapshots, content, and active sessions.
The privacy notice matches implementation and discloses retention exceptions.

Dependency: Sprint 4 accepted. Demo: use two accounts to verify private/public
boundaries, then export and delete a synthetic account.

### Sprint 6: add controlled social participation

Goal: introduce discovery through people with abuse controls in place.

- PG-060: follow/unfollow and a bounded activity feed.
- PG-061: blocking, reporting, and a moderation queue.
- PG-062: least-privilege moderator roles and logs of privileged actions.
- PG-063: apply privacy, deletion, and blocking consistently to social reads.

Acceptance: blocked or private activity cannot leak through feeds or direct
URLs. Ordinary users cannot reach moderation actions. Moderator decisions are
recorded without credentials or unnecessary private data. Reports and abusive
writes have enforceable budgets and an operator handling process.

Dependency: Sprint 5 accepted. Split social and moderation work into separate
sprints if needed, but release them together before opening participation.

### Sprint 7: qualify a controlled beta

Goal: operate the product with known release and recovery procedures.

- PG-070: configure HTTPS, trusted proxy handling, secret storage, and restricted services.
- PG-071: establish backups, restore rehearsal, alerts, and an incident procedure.
- PG-072: verify request budgets, upstream API budget, and realistic multi-user load.
- PG-073: run complete browser/regression tests and an independent security review.
- PG-074: close release-blocking defects and prepare deployment/recovery notes.

Acceptance: staging reproduces the deployment environment. Real Steam/Redis
flows pass, a backup restore works, secrets stay out of logs, and response time
and error targets agreed at planning are met under a recorded workload. No open
critical or high-severity security defects remain. Any deferred issue has an
owner, impact statement, and explicit release decision.

Dependency: the earlier gates are accepted. Operator MFA, branch protection,
hosting restrictions, and a successful CI run must be verified, not merely
described in configuration files. Roll out to a small invited group first.

## Later backlog

Recommendations, additional review sorts, richer statistics, public list
discovery, and nested replies come after the core loop and operating baseline.
Do not add personal Steam API-key collection, uploads, payments, or direct
messages without a separate product and threat review.

## Git and review workflow

Use a short feature branch for each focused ticket or tightly related group.
Keep main runnable. Separate behavior changes, migrations, and unrelated cleanup
when they can be reviewed independently. Avoid combining an entire sprint into
one enormous diff.

Each pull request describes the problem, final behavior, acceptance evidence,
security/data implications, and any deployment steps. Run automated checks and
review the diff before merge. Require staging evidence when a change touches
login, external services, migrations, or browser sessions.

Commit and push only at the agreed handoff point. A local implementation is not
the same as a merged or released feature. Record those states separately.

DEVELOPMENT_PLAN.md and SECURITY.md are repository documentation. ROADMAP.md and
HANDOFF.md are local notes excluded from Git. Repository documentation must
not depend on those ignored files for essential setup or operating instructions.

## Defect and scope policy

- P0: exposed credentials, account takeover, data loss, or a broken core service.
  Stop feature work, contain the issue, and verify recovery before resuming.
- P1: a core flow is unusable or an authorization/privacy boundary is incorrect.
  Fix before the next release and reconsider current sprint scope.
- P2: a limited defect with a practical workaround. Prioritize by impact.
- P3: polish and optional improvements. Keep in the backlog until capacity exists.

New ideas enter the backlog. Mid-sprint additions replace planned work only
through an explicit scope decision. A regression requires a focused prevention
check and investigation of the cause, not just a passing health endpoint.

## Ticket template

ID and title:
Owner:
Sprint and rough size:
User outcome:
In scope / out of scope:
Dependencies and decisions:
Acceptance checks, including denied/failure cases:
Data, security, and migration impact:
Verification evidence:
Branch / pull request:
State: Backlog / Ready / In progress / Review / Verify / Done / Blocked

## Sprint record template

Sprint and dates:
Goal:
Available capacity:
Committed tickets:
Demo and acceptance evidence:
Open defects and blockers:
Deferred scope:
Merge and deployment status:
One process improvement:
Next planning decision:

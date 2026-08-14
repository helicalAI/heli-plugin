# Implementation Plan — Helical Platform Plugin (metered individual embeddings via MCP)

Companion to [`DESIGN.md`](DESIGN.md). Design decisions live there; this document sequences the work into milestones and tickets across three repos: `dashboard`, `infra`, `heli-plugin` (plus k8s manifests in the `configs` repo).

Status: planned · Last reviewed: 2026-07-30 · Tracked as GitHub epic [helicalAI/dashboard#1756](https://github.com/helicalAI/dashboard/issues/1756)

## 0. Decisions and facts this plan is built on

Decisions (from DESIGN.md, confirmed by the product owner):

- Full program, phased; each milestone independently landable and flag-gated.
- **Token accounting is deterministic**: each row/cell costs a fixed number of tokens per model (data-prep + tokenizer dependent). Total = per-row factor × row count (× epochs for fine-tuning, × genes-to-perturb for perturbation, later). Estimate ≡ actual, so **billing needs no dags-repo change** and the debit can happen at launch.
- Prepaid credits; **Stripe** top-up behind a provider interface (#1838, decided); free monthly grant supported in schema, enablement is a launch parameter.
- **Two metering systems coexist, and they treat a deleted run oppositely** (DESIGN §5.6). ISP credits are *derived* from `run_meta`, so a delete erased the charge — hence #1927/#1978's soft delete. The money ledger is *recorded* in its own append-only table, so it does not need that protection and must not be built assuming it. They overlap only at `dag_id = 'perturbation'`, i.e. roadmap stage 3.
- **Current internal distribution is pro forma.** `.mcp.json` launches the local STDIO
  server in its default preview mode: discovery exposes the planned catalogue, while every tool
  call returns `Not implemented yet` before tool-schema or business validation,
  credentials, or networking. The
  packaged skills stop at an explicit preview gate and disable implicit invocation. The
  unfinished API adapter requires `--reference-adapter`. This permits marketplace and
  workspace testing without implying that the dashboard or billing surface exists.
- **Downloads return run outputs only, and uploaded inputs are never deleted** (#1972, DESIGN §5.7). Storage is therefore a monotonic, unpriced cost line that the token ledger cannot see, and a per-user cap is an open decision rather than a detail.
- **Confirmation reuses the existing `PendingConfirmation` queue, carried over MCP** rather than rendered in a dashboard chat (DESIGN §6.1). `start_*` prices, enqueues and returns the confirmation; a B2C-only `resolveConfirmation` tool approves or rejects and returns the `run_id`. Supersedes the earlier prompt-level decision, whose only real blocker turned out to be the rendering. Mitigations still hold as second line: balance ceiling, per-user concurrent-run cap.

Load-bearing repo facts (verified 2026-07-27):

| Fact | Consequence |
|---|---|
| `helical-mcp` targets `/api/mcp/trpc/*`, deleted from the dashboard in `233f4aa8` (2026-07-01). Every tool 404s. | It is a **PoC remnant, superseded by `agentcore-mcp`** and not being revived. The MCP endpoint lives in the dashboard (M3). |
| Live dashboard pattern: `src/app/api/agentcore-mcp/` — `defineGetTool`/`definePostTool` + Zod + `authenticateUser` bearer auth + singleton OpenAPI registry. | Reuse the lib with an injected registry for a new, separate route group so the enterprise chat gateway never sees the new tools. |
| `_helpers.ts` exports **unused** `triggerValidated(input, user, projectSlug)` → `airflowServices.triggerDagRun` (validation, `output_dir` stamping, `assertTriggerProjectAccess`, `RunMeta` write inside). | The direct-launch path already exists; `startEmbeddingRun` wraps it. |
| `auth.syncUser` → `addUserToDefaultProject` (`src/lib/auth-db.ts`) is the single login-time membership hook (currently joins hard-coded `pilot`). | Natural seam for per-user project auto-provisioning. |
| `recordDagRunMeta` silently skips the `RunMeta` write unless the caller holds **EDITOR** membership. | Auto-provisioned membership must be EDITOR or billing/lineage silently breaks. |
| **No presigned URLs exist** (uploads proxy bytes through `/api/s3/multipart`; downloads limited to `.pdf`/`.csv`). `@aws-sdk/s3-request-presigner` not a dependency. | Presigned PUT + GET endpoints are net-new. |
| `dataSc.create` copies staged files via `fs.copyFile` on the S3 fuse mount (`src/lib/relocateDatasetFile.ts`). | Needs an S3 `CopyObject` branch for mountless deployments (switch exists: `storageBackend.useLocalFiles`). |
| The **dags repo writes `terminal_state`/`execution_time` directly to Postgres**; dashboard self-heals via `reconcileActiveRuns` on read. No webhook. | Billing settlement must be lazy (on read) + a sweep job; cannot rely on a dashboard-side completion callback. |
| `src/lib/cognito-bearer-auth.ts:37` verifies with `clientId: null` (any client in pool; acknowledged TODO). | Must be tightened to an env-driven allowlist + scope check before exposure. |
| Infra tenant = 4 files copied from `envs/stage/qa-tenant/` + gitignored tfvars (23 secrets/URLs). Pool has **no lambda triggers**. Cognito attribute `required` flags are immutable (change ⇒ pool replacement). | Self-signup + attribute relaxation must be correct at tenant birth; provisioning is lazy in-dashboard, not a Cognito trigger. |
| `envs/dev/helical-mcp-cpl/` builds branch `develop` of `helicalAI/helical-mcp` — **which does not exist**; no `buildspec.yml`, no ECR repo in TF, no k8s manifests anywhere. | Nothing to deploy — the pipeline is dead scaffolding for a superseded PoC and can be removed. |
| Browser services' ALB ingress uses `alb.ingress.kubernetes.io/auth-type: cognito`. | The MCP ingress must NOT copy those annotations (bearer-only, non-browser clients). |

---

## Milestone 0 — Foundations (added 2026-07-30; land before M1)

Two design sections were added after this plan was first written — §2.4 (extend the platform rather than build a lean service around the DAGs) and §2.5 (parametrising what each edition exposes). Both produce prerequisite work.

### 0.1 Shared DAG-contract package — #1831 · **dropped, closed as not planned**
It guarded against a second caller re-deriving the `conf` contract. There is no second caller: the B2C path reuses the existing implementation end to end (§2.3), so the drift it protects against cannot occur. The cost was also higher than assumed — 88 files import `airflow-constants`, 67 import `constants/paths`. The couplings it named (a `lucide-react` import in the DAG constants, a UI type in `paths.ts`) are real but only bite something outside Next importing them; split them out then, on their own merits.

### 0.2 Tenant capability manifest — #1832
`src/config/tenants.ts` keyed by `NEXT_PUBLIC_NAMESPACE`, mapping each deployment to `edition: "b2b" | "b2c"` plus explicit per-tenant overrides. Fail at boot on an unknown namespace (the repo has no env validation today). Absorb the eight existing ad-hoc namespace checks, including the tenant array duplicated between `PlatformContext.tsx:154` and `airflow-services.ts:185`.

### 0.3 Edition enforcement — #1833
Root-level tRPC path allowlist (deny-by-default, `NOT_FOUND` for unclaimed routers) + `featureProcedure(capability)` for finer gates; widened `middleware.ts` matcher with a manifest-driven page-prefix check; `Sidebar.tsx` refactored to a data-driven `NAV_ITEMS` array. Do **not** prune the root router object — `AppRouter` is the client's type source. Jupyter/Coder and cross-indication porting become absent capabilities rather than bespoke conditionals. CI: capability completeness, "every route claimed by an edition", per-edition snapshots.

### 0.4 B2C edition surface — #1834
`src/app/(b2c)/` route group for the four account screens — sign-up, sign-in, top-up, balance (DESIGN §2.5); the balance page and the `getBalance` tool (#1767) read one shared aggregation rather than computing it twice; `hideSignUp={!features.selfServeSignup}` in `authenticator.tsx`; scheduled reconciliation of the resolved sign-up capability against the pool's `admin_create_user_config`. Verify the `pilot`-project EDITOR landmine is absent in production.

**Sequencing:** 0.2 must land with M4.3 (the tenant's dashboard config declares the namespace, and boot fails on an unknown one). 0.4 must land with M4.1 (`allow_self_signup`), or app and identity provider disagree silently. 0.1 precedes anything that stamps `output_dir`, resolves a model name, or validates a DAG config.

## Endpoint inventory — everything the two workflows touch

Audited against `dashboard/src/app/api/agentcore-mcp/` and the tRPC routers. **Port** = the route exists and needs only the §2.3 treatment. **New** = nothing exists; it has to be written.

All target paths are relative to the B2C route group, `/api/platform-mcp`. Paths marked **(proposed)** do not exist anywhere yet and are this plan's specification — they are what the plugin's scaffold already calls, so changing one means changing both. Ported paths deliberately keep their `agentcore-mcp` shape so the diff stays reviewable, minus the scope segment.

### Datasets

| Capability | Target path | Today | Work | Ticket |
|---|---|---|---|---|
| List datasets | `GET /data` | ✅ same | Port | #1761 |
| Get a dataset | `GET /data/{id}` | ✅ same | Port | #1761 |
| Dataset columns / genes / obs values | `GET /data/{id}/columns`, `/genes`, `/obs-values` | ✅ same | Port — `columns` and `obs-values` are how fine-tuning's label column is verified | #1761 |
| **Request an upload** | `POST /data/uploads` **(proposed)** | ❌ | **New** — presigned PUT; needs `@aws-sdk/s3-request-presigner`, which is not yet a dependency | #1761 |
| **Register an upload** | `POST /data` **(proposed)** | ❌ tRPC `dataSc.analyzeUpload` + `dataSc.create` only; bytes proxy through `/api/s3/multipart` | **New** — wraps the existing services; needs an S3 `CopyObject` branch in `relocateDatasetFile.ts`, which is `fs.copyFile` on the fuse mount today | #1761 |

### Models

| Capability | Target path | Today | Work | Ticket |
|---|---|---|---|---|
| List models | `GET /models` | ✅ same | Port | #1760 |
| **Get one model** | `GET /models/{id}` **(proposed)** | ⚠️ list only | **New**, small — fine-tuning returns a model id with nothing to resolve it against | #1760 |
| Promote / demote | `POST /models/promote` | ✅ same | Port — but confirm it means anything for B2C, where there is no workspace to promote between | #1760 |
| **Rename** | `POST /models/{id}/rename` **(proposed)** | ⚠️ tRPC `mlflow.renameModelVersion` only | **New** route over the existing service. Identifier mismatch to resolve: the service takes `mlflowRunId`, the surface uses `model_id` | #1840 |
| **Upload a model** | — | ❌ no precedent anywhere | **Scope decision first** — see the note below | #1841 |

### Estimates — one per operation

| Capability | Target path | Today | Work | Ticket |
|---|---|---|---|---|
| ~~Estimate a run~~ | — | ❌ | **Dropped.** `start_*` mints the quote and returns it in the confirmation, so a separate estimate tool prices something the caller cannot then spend. The estimator stays as an internal service (DESIGN §5.2) | #1766 |

### Runs

| Capability | Target path | Today | Work | Ticket |
|---|---|---|---|---|
| Compute embeddings | `POST /airflow/trigger/embedding` | ✅ `…/embedding/{conversationId}` | Port — scope from the subject; **keeps** enqueuing, and returns the confirmation with the quote embedded | #1762 |
| Fine-tune | `POST /airflow/trigger/finetuning` | ✅ `…/finetuning/{conversationId}` | Port — same treatment | #1762 |
| List runs | `GET /airflow/runs` | ✅ same, but `projectId` required | Port — scope derived, so the parameter disappears | #1762 |
| Run details (+ artifacts) | `GET /airflow/run-details/{runId}` | ✅ same | Port — already scopes from the run row | #1762 |

### Outputs

| Capability | Target path | Today | Work | Ticket |
|---|---|---|---|---|
| List / read output files | `GET /files/list`, `/files/read`, `/s3` | ✅ same | Port — `read` stays UTF-8 ≤ 1 MiB | #1763 |
| UMAPs | `GET /airflow/umaps`, `/airflow/umaps/{runId}` | ✅ same | Port | #1763 |
| **Download an artifact** | `GET /artifacts/{artifactId}/download` **(proposed)** | ❌ | **New** — presigned GET. One endpoint serves **both** embeddings and model checkpoints: they are `ArtifactMeta` rows differing only in `artifact_type`, so "download my embeddings" and "download my model" are the same route | #1763 |

### Account

| Capability | Target path | Today | Work | Ticket |
|---|---|---|---|---|
| **Balance and top-up link** | `GET /billing/balance` **(proposed)** | ❌ | **New** | #1767, #1776 |

Cost estimation is **one endpoint per operation**, not one shared endpoint. The token count is a different function in each case — rows × width × coefficient for embedding, × epochs for fine-tuning, × genes perturbed for perturbation — and each takes the parameters of the run it prices, so a single endpoint would need either a discriminated union (which the AgentCore gateway rejects, and which is exactly why the trigger routes are already split per DAG) or a lowest-common-denominator body that cannot express any of them properly. Every operation added under §7.0 ships with its estimator in the same change; an operation that can be started but not priced would breach the estimate-before-spend rule in §6.1.

Two invariants the paths encode, both asserted by tests in `heli-plugin`:

- **No scope in any path, query, header, or body** (§2.3.1) — which is why the ported trigger paths lose their `{conversationId}` segment and `listDagRuns` loses `projectId`.
- **`start_*` takes no `quote_id`.** It mints the quote itself and embeds it in the confirmation it returns, so the quote necessarily prices exactly what will run and a caller cannot pair one run with another's price (DESIGN §6.1). The quote is fixed at request time and honoured on approval; the confirmation TTL and the quote window are the same number, so an approval can never land on a stale price.

Two things this audit surfaced that were not previously tracked:

- **Model rename** (#1840) exists only as a tRPC procedure and is keyed on `mlflowRunId` while the rest of the surface uses `model_id`. Exposing it means resolving one to the other, or accepting an inconsistent identifier in one tool.
- **Model upload** (#1841) has no precedent anywhere in the platform — MLflow is populated exclusively by fine-tuning runs. This is not an endpoint, it is a feature: validating an uploaded artifact, registering it in MLflow, deciding what a user-supplied model may be used for, and pricing it, since §5.1 prices per token by *model coefficient* and an unknown model has none. **It needs a scope decision before it is estimated** — it may well not belong in the first release.

## Milestone 1 — Dashboard: the second MCP surface + user provisioning

Everything gated by env `INDIVIDUAL_TENANT=true`; flag off ⇒ zero behavior change.

**Nothing here is a port, a copy, or a wrap.** One tool definition lives in
`src/lib/mcp/tools/<domain>/<tool>.ts`; each surface registers it with its own middleware
injected, and the route file is the registration and nothing else — import the shared tool,
hand it the surface's `ProjectSource`, register with the surface's factory. Three lines.
`agentcore-mcp` and `platform-mcp` differ in **which tools they register**, **how the project
is resolved**, and — B2C only — **the billing debit**. Below those seams the path is identical:
same services, same DAGs, same Airflow, same database.

The "Port" column in the inventory above means "this endpoint already exists and gains a second
registration", never "this endpoint gets a B2C twin". If a difference cannot be expressed as an
injected seam, that is the signal to widen the seam, not to fork the handler.

### 1.1 Generalize the tool-definition lib — **shipped** (#1757)
- Extract `src/app/api/agentcore-mcp/_lib/define-tool.ts` + `_lib/registry.ts` so the `OpenAPIRegistry` is injected (e.g. `makeDefineTool(registry)`) instead of imported as a singleton. `agentcore-mcp` behavior stays byte-identical (its spec, its S3-published OpenAPI, its tests).
- The injectable seams that followed: `ProjectSource` (`src/lib/mcp/project-source.ts`) for scope, `run-scope.ts` for run reads, and the per-surface `_lib/` instantiations of each. These are the middleware; adding a surface means adding instantiations, never handlers.
- New group `src/app/api/platform-mcp/` with its own `_lib/` (own registry), own `openapi.json` route (bearer-gated), own README documenting conventions.
- Port the registration-drift test pattern (`tests/agentcore-mcp/route-registration-drift.test.ts`) for the new registry.

### 1.2 Bearer-auth hardening
- `src/lib/cognito-bearer-auth.ts`: replace `clientId: null` with allowlist from `COGNITO_ALLOWED_CLIENT_IDS` (comma-separated) and require scope `COGNITO_REQUIRED_SCOPE` when set. Absent env ⇒ current behavior (existing tenants unaffected).
- Tests: wrong-client, wrong-scope, and legacy-mode acceptance.

### 1.3 Individual project auto-provisioning
- `src/lib/individual-project.ts`: `resolveIndividualProject(prisma, user)` — transactional upsert of `Project { slug, name }` where the slug follows the indexed convention `user-<short-cognito-sub>-01` (satisfies the existing regex `^[a-z0-9]+(?:-[a-z0-9]+)*$`; immutable — it is a security principal in S3 paths). The index is deliberate: one user's projects sort together and the owner is recoverable from the slug alone. Ship one, but do not make a second unrepresentable. + `ProjectMembership { role: EDITOR }`.
- Wire into (a) `addUserToDefaultProject` (replaces the `pilot` join when `INDIVIDUAL_TENANT=true`), and (b) `requireSubjectProject(user)` — the per-request resolver every `platform-mcp` handler calls, so first authenticated MCP request provisions (DESIGN §4.4).
- **The MLflow workspace is NOT part of this milestone** (#1835 moves to roadmap stage 2). `embedding_dag.py` contains no MLflow reference; MLflow enters only through `db/finetuning.py` and the Nebius fine-tuning DAGs. What `assert_project_access` requires is `metadata.project_id` (a `project_membership` row must join the triggering `cognito_sub` to the project) and `metadata.project` (every `/projects/<slug>/…` path in the conf must belong to the run's own project) — data-path scoping, not workspace scoping. Automating workspace creation is still real work; it gates fine-tuning, not signup.
- Membership re-check per DESIGN §2.3: the resolver + in-service checks, not just route-edge.
- Tests: idempotency under concurrent first requests; slug shape; EDITOR role present; flag-off ⇒ untouched.

### 1.4 Models + datasets tools
- `listModels` — wraps `listModels` (`src/trpc/routers/models/list.ts`), promoted default, family filter, joined with `ModelPrice` (M2) to include `price_per_million_tokens`.
- `createDatasetUpload` — net-new presigned PUT (add `@aws-sdk/s3-request-presigner`): short-lived URL under `datasetUploadPrefix(slug, username)`; extension validated against `ALLOWED_DATA_EXTS`, size cap.
- `registerDataset` — lift `dataSc.analyzeUpload` + `dataSc.create` procedure bodies into a shared service (they are inline today); add an S3 `CopyObject` branch to `src/lib/relocateDatasetFile.ts` behind `storageBackend.useLocalFiles`. Returns dataset with `cellCount`/`geneCount` (estimator inputs). **Also persist the object's byte size** — `DataScMeta` has no size column today (`sizeBytes` in the schema belongs to `AgentOutputArtifactMeta`), the file is retained indefinitely, and reconstructing the figure later means walking S3 per user (#1761).
- `listDatasets` / `getDataset` — port of `agentcore-mcp/data/` handlers, subject-project-scoped.

### 1.5 Embedding-run tools
- **No `estimateEmbeddingRun` tool.** The estimator is an internal service called by `startEmbeddingRun`; exposing it separately would offer a price the caller cannot act on, and a second way to produce the number that gets charged.
- `startEmbeddingRun` — `requireSubjectProject` → price and write an `EstimateQuote` → `requestConfirmationService(...)` with the quote and the replayable launch in `payload`. **Starts nothing, charges nothing**, and returns the confirmation: id, quote, resolved parameters, phrase, expiry. Safe to retry — a repeat costs another quote, not another run.
- `resolveConfirmation` — approve or reject. Approval runs inside the existing `resolveConfirmationService` claim (`status: pending` guard, proceed only when exactly one row updated), so concurrent approvals launch once; `executeConfirmation` then does M2 `debitForRun` and `triggerValidated(...)` in one transaction and returns the `run_id`. Enforces `MAX_CONCURRENT_RUNS_PER_USER` at approval, not at request. **Registered on `platform-mcp` only** — on the enterprise surface a human resolves in the dashboard, and an agent able to approve its own request would defeat that queue.
- **Schema change this needs**: `PendingConfirmation.conversationId` is `NOT NULL` and cascades from `Conversation`, which a B2C caller has none of. It becomes nullable, with a subject-scoped form of `uq_pending_confirmation_live` and the scope injected the way `ProjectSource` is. Note `target` must include the model, or a second run on the same dataset is refused by the live-uniqueness index for the wrong reason.
- `listEmbeddingRuns` / `getRunStatus` — `runs/list.ts` + `pollActiveStatus`; `getRunStatus` invokes M2 `settleRun` when it observes a terminal state; terminal responses include tokens + charge.

### 1.6 Results tools
- `listResults` / `searchResults` — `ArtifactMeta` rows joined to the subject's runs; filters: model, dataset, date, free-text on `displayName`.
- `downloadResult` — net-new presigned GET for `ArtifactMeta.s3Key` via `mountPathToBucketKey` + `getS3Client`/`getTenantBucket` (`src/lib/s3Utils.ts`); short-lived, subject-bound. **Signs output keys only** (#1972): it resolves the key from an `ArtifactMeta` row rather than from a caller-supplied path, so an uploaded dataset has no row to name. `readFile` and `listS3Files` walk the project prefix and are the way around that, so they need the same input/output split — otherwise the rule holds only for the one tool that states it.

### 1.7 Isolation test suite
- Cross-user denial for every tool (unauthorized ≡ nonexistent in status/shape/wording); `projectScopeRule` ESLint clean; `docs/DATA_SEPARATION.md` checklist walked for each new data-touching path.

## Milestone 2 — Dashboard: billing schema + metering services

Prisma per `prisma/README.md` rules: inline model comments, ERD + Ownership-boundaries table updated in the same change, hand-authored migration names, read-only `/dev/<table>` routes.

### 2.1 Schema
- `ModelPrice` — `{ id, model, version (default ""), operation (embedding|finetuning|perturbation), tokensPerRow Decimal(12,4), priceMicroUsdPerMToken BigInt, active, effectiveAt }`; seeded by migration.
- `CreditLedger` — append-only: `{ id, userId, entryType (topup|grant|debit|refund|expiry), creditClass (paid|free), amountMicroUsd BigInt (signed), tokens BigInt?, runMetaId? , quoteId?, periodKey?, providerRef?, createdAt }`. Partial unique indexes: `(runMetaId, entryType)` for debit/refund idempotency; `(userId, periodKey, entryType)` for once-per-period grants; unique `providerRef` for webhook idempotency.

  Two corrections from building it (#1917): the amount and token columns are **`BigInt`, not `Int`** (int32 tops out at $2,147.48 in micro-USD, and below the token count of a single multi-million-cell dataset), and the partial indexes are **declared in `schema.prisma`**, not hand-written into the migration. `previewFeatures = ["partialIndexes"]` is already enabled and `uq_project_pilot` already uses it; writing them only in SQL means the next `prisma migrate dev` drops all three. The migration is still hand-authored for the CHECK constraints, which the datamodel genuinely cannot express, and its index predicates must match Prisma's own rendering byte-for-byte (`WHERE ("active" = true)`, not `WHERE "active"`) or `prisma-check` fails on drift.

  `runMetaId` must be **`ON DELETE RESTRICT`**: a billed run should not be deletable out from under its ledger entry, because `SET NULL` blanks the discriminator that the debit/refund idempotency index and the `credit_ledger_discriminator_present` CHECK both depend on — the CHECK would reject the very update the FK performs. The #1917 branch still carries `SET NULL`; correcting it is outstanding on that PR, and `prisma/README.md` there documents the `SET NULL` rationale in prose, so both move together or the README argues against the schema.

  **`RESTRICT` needs a deliberate answer for the four shipped delete paths**, not just a schema line: `runs/delete.ts`, two sites in `runs/index.ts`, and `benchmarks/index.ts` all delete `RunMeta` today. This is not a regression the change introduces — under `SET NULL` those deletes already fail once a ledger row exists, because the FK's own `UPDATE` violates `credit_ledger_discriminator_present`; they merely fail as a raw check violation (23514) instead of a foreign-key error. Either way the caller needs a typed "this run has been billed" response rather than a database error surfacing. Worth noting that `docs/COST_CONTROL.md` records "deleting a run erases its usage" as a live self-service abuse, which `RESTRICT` is what closes.
- `EstimateQuote` — `{ id, userId, modelPriceId (FK), datasetId, operation, tokens BigInt, priceMicroUsdPerMToken BigInt, totalMicroUsd BigInt, assumptions Json, expiresAt, createdAt }`. The quote points at the `ModelPrice` row it was struck against rather than copying the model name and version, which is what makes "the quoted price is honoured across a price-table change" a property of the data instead of a rule the service has to remember.

**Every money and token column in all three models is `BigInt`** — not only `CreditLedger`'s. `totalMicroUsd` is where the int32 argument bites hardest, since a single quote can exceed $2,147.48.

### 2.2 Services (`src/lib/billing/`)
- `computeTokens(operation, model, dataset)` = `cellCount × tokensPerRow` (embeddings; formula gains epoch/gene factors for later operations).
- `createEstimate(user, input)` → quote row (inputs already on `DataScMeta` from `analyzeUpload`).
- `debitForRun(tx, user, quote, runMetaId)` — charge at launch: row-locked balance read + debit insert in one transaction; free class consumed before paid; honors unexpired quote pricing; typed `insufficient_balance` error (shortfall + top-up URL) mapped to the DESIGN §6 shape.
- `settleRun(runMetaId)` — idempotent; refund entry on terminal `failed`/`cancelled` (charge only completed work). Called from `getRunStatus`/`reconcileActiveRuns` read paths + a sweep script (`scripts/`, cron-able) because terminal state is written by the dags repo directly.
- `grantMonthlyFreeCredit(user)` — no-op unless `FREE_MONTHLY_TOKENS` set; lazily invoked from balance reads; `(userId, periodKey)` uniqueness = once per period; period rollover writes `expiry` entries for unspent free credit.
- `getBalance` / `getUsage` tools (ledger aggregation + existing `users/usage.ts` rollups).

### 2.3 Tests
Debit/refund idempotency (incl. DAG retries), concurrent-kickoff no-overdraft race, free-before-paid ordering, grant non-accumulation, quote expiry, quoted-price honored across a price-table change.

## Milestone 3 — Dashboard: the native MCP endpoint — #1912

`helical-mcp` is a remnant of a past proof of concept, superseded by the `agentcore-mcp` pattern; there is no separate transport service. The MCP endpoint lives in the dashboard, in the same route group as the tools it serves. Tickets #1768–#1771 and #1775 are closed as not planned.

### 3.1 The protocol route
- `src/app/api/platform-mcp/mcp/route.ts` speaking **MCP Streamable HTTP**: JSON-RPC over POST, handling `initialize`, `notifications/initialized`, `tools/list`, `tools/call`. Nothing in the dashboard does this today — a grep for `jsonrpc` / `tools/list` / `StreamableHTTP` across `src/` returns nothing — so this is net-new protocol code, not a port.
- **Stateless**: issue no `Mcp-Session-Id`. The dashboard runs multiple replicas behind an ALB with no session affinity, so a session-bearing implementation needs sticky routing or shared state. If session state proves necessary, record the decision.

### 3.2 Descriptors generated, never written twice
- `tools/list` is generated from the same Zod schemas that define the REST routes, through the injectable registry from M1.1 — with a registration-drift test mirroring `tests/agentcore-mcp/route-registration-drift.test.ts`.
- `tools/call` dispatches to the same handler functions the REST routes call, so there is one implementation and one authorization path per tool.
- The no-`oneOf`/`anyOf` rule does **not** apply here: it exists because the AgentCore Gateway rejects them, and this endpoint has no gateway. Keep closed schemas (`additionalProperties: false`) anyway — those are for the model, not the gateway.

### 3.3 Auth and discovery
**Authentication follows the existing OAuth proxy** (`infra/modules/tenant/user_pool_client_mcp.tf`), which is the source of truth for this design — see DESIGN §4. The proxy presents the DCR-compliant surface Cognito cannot, fakes registration onto its one confidential client, and proxies Authorization Code + PKCE to the hosted UI.

- RFC 9728 metadata and the `401` + `WWW-Authenticate` challenge are the **proxy's** responsibility, not this endpoint's.
- Bearer verification reuses `cognito-bearer-auth.ts` with a **new surface** (`"platform-mcp"`), per the per-surface policy from M1.2 — not the `mcp` surface, whose allowlist is the Gateway's. The allowlist contains the **proxy's** client id.
- The token the dashboard sees is an ordinary Cognito access token; the proxy holds no privilege of its own.

### 3.4 Tests
Protocol tests (`initialize` handshake, `tools/list` drift, `tools/call` dispatch), the 401 challenge and metadata document, and cross-user isolation through the endpoint as well as the REST routes.

## Milestone 4 — Infra: individual tenant and Cognito

### 4.1 Self-signup module change
- New `allow_self_signup` (bool, default false, modeled on `enable_sso`).
- `user_pool.tf:90` → `allow_admin_create_user_only = !(var.enable_sso || var.allow_self_signup)`; `given_name`/`family_name`/`phone_number` `required = var.enable_sso || var.allow_self_signup ? false : true`.
- Regression gate: `terraform plan` renders **no changes** for every existing tenant (both expressions are identity-preserving for `(enable_sso, allow_self_signup=false)`).

### 4.2 MCP app client
**No new client.** `modules/tenant/user_pool_client_mcp.tf` already provisions the confidential OAuth-proxy client, gated on `enable_helical_mcp` and enabled on `stage-tenant`. Adding a second would collide on the `aws_cognito_user_pool_client.mcp` address and destroy its Secrets Manager entry on apply.

- For the individual tenant, set `enable_helical_mcp = true` and `helical_mcp_public_url`.
- The only Cognito-registered callback is the proxy's `<public_url>/auth/callback`; a client's own loopback redirect is handled by the proxy, so Cognito's lack of wildcard ports never arises.
- Scope is the existing `agentcore/invoke`; no second resource server.

### 4.3 Individual tenant env dir
- `envs/dev/individual-tenant/` (dev first): copy `envs/stage/qa-tenant/{main.tf,variables.tf,data.tf,outputs.tf}`; fix `data.tf` remote-state paths (dev eks, region globals); set `namespace`, `cognito_auth_sudomian` (expect the `amazoncognito.com` fallback if the 4-per-region custom-domain cap is hit), `allow_self_signup = true`, and `enable_helical_mcp = true` + `helical_mcp_public_url` to enable the existing OAuth-proxy client (§4.2); gitignored tfvars (23 secrets/URLs). Also set `COGNITO_PLATFORM_MCP_ALLOWED_CLIENT_IDS` to that proxy client's id in the configs repo.
- Dashboard env for this tenant (configs repo `application.yaml`): `INDIVIDUAL_TENANT=true`, `COGNITO_ALLOWED_CLIENT_IDS`, `COGNITO_REQUIRED_SCOPE`, optional `FREE_MONTHLY_TOKENS`, `MAX_CONCURRENT_RUNS_PER_USER`.

### 4.4 Ingress posture for the MCP path
No separate service to build or deploy — the endpoint ships with the dashboard, so there is no image, ECR repo, k8s manifest or ArgoCD app. The one carried-over constraint: whatever ingress fronts the MCP path must **not** carry the browser `alb.ingress.kubernetes.io/auth-type: cognito` annotations, because these are non-browser bearer clients that cannot complete a redirect login.

## Milestone 4b — GPU access strategy (AWS vs Nebius vs managed) — #1837

Per DESIGN §5.4. Not an ops detail: per-token pricing means we absorb all compute-cost variance, and three consequences reach billing correctness.

- **Spot vs on-demand for the consumer namespace.** Spot/on-demand is selected by *tenant namespace name* (`novartis`/`mt`/`pfizer` get on-demand, everything else spot), so a new individual tenant lands on spot — and the embedding DAG configures **no retries**. An interruption becomes a failed run, which per §5.1 we refund, so we pay for the GPU and collect nothing. Decide: add the namespace to on-demand, or accept spot and add a retry policy. The two provider lists have already drifted (embedding's omits `dev`, fine-tuning's includes it) — reconcile.
- **Pin the image the coefficients were measured against.** AWS and Nebius pin different bio-agent image tags; the per-row token coefficient is a property of the model's data prep and tokenizer, so it is not automatically valid across images. Re-verify on any provider or tag change.
- **Artifact path prefix differs by provider** (`/datasets/` on the Nebius return path vs `/projects/` on AWS) — `downloadArtifact` and artifact indexing handle both, or the tenant pins one provider.
- **Remove `node_type`/`num_devices` from `startEmbeddingRun`** (M1.5): the platform picks the compute profile the coefficient was priced against, otherwise a caller can multiply our cost at a fixed price.
- **Evaluate the managed options** against measured GPU-hours, not list prices: **Modal** is the designated escape hatch (per-second, scale-to-zero, and the embedding compute is a single containerised CLI so only one task moves); **Baseten** is serving-oriented and a partial fit; **Runware** was evaluated and ruled out — it is a generative-media inference API whose bring-your-own-weights is limited to diffusion artifacts, with no arbitrary containers or batch GPU compute, so it cannot run the bio-agent image at all.

## Milestone 5 — Dashboard: Stripe top-up (#1776)

- **The processor is Stripe** (#1838, closed). The provider interface stays anyway — it is what keeps Stripe vocabulary out of the ledger, not a hedge against a swap.
- `src/lib/billing/provider.ts` interface: `createTopUpSession(user, amountMicroUsd)`, `verifyWebhookEvent(req)`, `refund(providerRef)`; Stripe implementation (Checkout session bound to `userId`).
- The top-up page is Helical-owned on our domain; the provider's hosted checkout collects card details. We never render a payment form, and the MCP surface only ever hands out a short-lived URL (DESIGN §5.3).
- `POST /api/billing/webhook`: signature + freshness verification; `topup` ledger entry idempotent on `providerRef` = **Stripe's `event.id`** (delivery dedupe — Stripe redelivers after a non-2xx or timeout). `event.id` is *not* payment-level uniqueness: one session emits several events, each with its own id. Credit on `checkout.session.completed` **only when `payment_status === "paid"`** (delayed methods arrive `"unpaid"`), credit on `async_payment_succeeded`, record `async_payment_failed` without crediting, and carry the `payment_intent` id so payment-level uniqueness is expressible. Keying on the session id instead swallows the legitimate second event. Success/cancel pages.
- `insufficient_balance` responses embed the short-lived session URL.

## Milestone 5b — Metrics: adoption, retention, spend (#1971)

The ledger records what we charge, which is not the same as whether the product works. A user who signs up, lists models and never starts a run writes no ledger row at all — and that is the cohort a funnel exists to see. So the events are emitted server-side from the transactions that already own the facts (no client-reported steps; the MCP surface has no browser to carry an analytics SDK), and they land before distribution rather than after (DESIGN §5.8).

- **Adoption** — signup, first authenticated MCP call, first estimate, first started run, with the drop-off between each step.
- **Retention** — weekly/monthly returning cohorts, runs per active user, interval between runs. `User.lastActiveAt` cannot serve as the series: it is a single overwritten column and the write is throttled to at most once per 24 h (`ACTIVITY_STALE_MS` in `src/lib/auth-db.ts`), so it answers dormancy for the 90-day soft delete and nothing finer.
- **Spend** — top-ups, tokens by model, free-grant vs paid consumption, refunds, unspent balance — aggregated from `CreditLedger`, set against the per-user cost side (GPU-hours **and** stored bytes, M5c).
- Per-user data is internal-only under DESIGN §9: no user-facing response carries another user's activity or a cross-user total.

## Milestone 5c — Storage: the second cost line (#1972, #1761, #1888)

Two decisions taken together: **downloads return run outputs only**, and **inputs are never deleted after a run**. The first stops the plugin being used as readable free cloud storage; the second is required because re-embedding with another model, re-estimating and any later fine-tune all read the original file. Together they leave us running write-only storage at our own expense — uncapped, unbilled, and today unmeasurable without walking S3.

Verified against `dashboard@develop`: `ProjectTypeQuota` has no storage column; `DataScMeta` records `cellCount` but no size; `modules/tenant` has no lifecycle rule on project data (the only `expiration` block is on the server-logging bucket); metering charges per token of compute, so stored bytes debit nothing.

- Record the byte size at registration (M1.4, #1761) — the prerequisite for every figure below.
- Report storage per user alongside GPU-hours (#1888). A user who uploads 50 GB, runs one small embedding and never returns is compute-positive and overall negative; a GPU-hours-only view calls them healthy.
- A per-user storage cap is an **open decision**, not a task (DESIGN §14): `ProjectTypeQuota` is where it would live, and adding a column there obliges every existing tier to get a real figure in the same migration: per `docs/COST_CONTROL.md` that table has no "unset" state and `0` **forbids** rather than meaning unlimited, so a `@default(0)` storage cap would block uploads platform-wide on day one.

## Milestone 6 — heli-plugin artifacts

- `.mcp.json`: remote Streamable HTTP entry pointing at the dashboard's MCP endpoint (STDIO scaffold retained for local dev).
- **Done ahead of the milestone**: the legacy article skills are removed and replaced by `skills/compute-embeddings/` and `skills/fine-tune-model/` (DESIGN §8.3), and `mcp/server.py` now implements the §7 tool contract for both — sixteen tools, with the estimate-before-spend split and the rejection of `node_type`/`num_devices`/`device`/`output_dir` asserted by tests. Remaining here: point `.mcp.json` at the dashboard's MCP endpoint (M3) for production.
- Settle the remaining publication items, all decisions rather than edits: the **privacy policy and terms URLs** (removed rather than guessed at — real paths needed), and whether **`capabilities: ["Read"]`** is accurate for a plugin that starts billable runs. Publisher, support, repository and **licence** metadata are done — the repo ships a proprietary, all-rights-reserved LICENCE. Note it grants an end user no right to run the plugin, which is correct for an internal repo but must gain an end-user grant before public distribution.

## Milestone 6c — Local execution skill (#1844)

Per DESIGN §7.2. A third skill, `run-helical-locally`, driving the open-source package (`pip install helical`, AGPL-3.0) on the user's own GPU through the agent host's shell and code execution. **No MCP tool, no route, no dashboard ticket** — the plugin's server cannot execute on the user's machine, so this is instructions only and declares no tool dependency.

- Preflight before install: Python **≥ 3.12 < 3.13** exactly, `nvidia-smi`, compute capability ≥ 8.9 for Evo 2, disk for a multi-GB torch/CUDA install. Caduceus is a hard blocker on CPU (`mamba_ssm` is CUDA-only).
- Extras per model: `helical[mamba-ssm]` for Caduceus/Mamba2, `helical[evo-2]` for Evo 2, `flash-attn --no-build-isolation` where used.
- Reaches four models the platform does not host — **Tahoe-X1, Caduceus, Evo 2, GenePT** — and therefore the ten reference prompts (§12.2) that have no hosted model.
- Same honesty rules as the hosted skills, plus two of its own: recommend a backend explicitly with a reason, and never compare a local result against a hosted one as a model comparison (different builds).
- Independent of everything else here: no port, no billing, no tenant, so it can land at any time.

## Milestone 6b — Launch readiness (#1836)

Two distribution blockers with no owner. **No customer-facing support or ticketing channel exists**, which makes the "contact support" branch of the §6 error table a dead end; a dedicated public issue tracker is the cheapest credible option. And the retention/deletion policy must be written to match reality on three counts: the account lifecycle already soft-deletes after 90 days of inactivity, so the policy has to state what that does to project contents and artifacts; **unspent credit** is a refund question as well as a data question; and **we hold uploaded inputs indefinitely with no way for the user to retrieve them** (M5c), so asking us to delete is their only control over that data and the policy must name the three paths that end retention — account deletion, erasure on request, and the inactivity boundary. One wording trap: the `CreditLedger` is append-only and outlives the artifacts it refers to, so "we deleted everything" must not be phrased in a way that a later, correct historical charge contradicts.

## Milestone 7 — End-to-end verification & dogfood

Acceptance run on `individual-tenant` (dev): hosted-UI self-signup → add the proxy URL in Claude Code/Codex and complete the browser login (dynamic registration against the proxy, Authorization Code + PKCE to Cognito behind it) → upload `.h5ad` → register → estimate → confirm → embedding DAG runs → status shows tokens + charge → artifact downloads → ledger/balance consistent. Second account proves isolation (all reads on user A's ids → identical 404 shape). DESIGN §12 is the test checklist; the confirmation-skipping-client scenario validates §6.1 mitigations.

---

## Ordering & parallelism

- **M0 first.** 0.1 unblocks anything touching the DAG contract; 0.2–0.4 unblock the edition split.
- M2 schema → M1 routes (one dashboard PR train behind the edition manifest).
- M4.1–4.2 (Cognito module changes, default-off) can land any time, subject to the two pairing constraints in M0.
- M3 (the MCP endpoint) needs M1.1's injectable registry and M1.3's subject scoping; the protocol layer can be built against a handful of read-only tools before the rest land.
- M4.3–4.4, M5, M6, M6b follow; M7 last.
- **M5b (metrics) lands before distribution, not after.** It is cheap and it is the only milestone whose value is destroyed by being late: the funnel steps that matter are the ones users never reach, and an unrecorded non-event cannot be backfilled.
- M5c's one blocking piece is the byte-size column in M1.4 — free at upload, expensive to reconstruct. The reporting and the cap decision can follow at any time.
- **M4b must be settled before the first paying user** — the spot default silently costs money on every interrupted run. M5's processor question is closed (#1838: Stripe).

## Per-repo verification

| Repo | Gate |
|---|---|
| dashboard | `npm run typecheck && npm run lint && npm run test` (incl. new `tests/platform-mcp/*`; `projectScopeRule` clean); `npx prisma migrate dev` clean; `/dev/<table>` routes render; `claude mcp add --transport http` against a local `npm run dev` with a Cognito bearer, then exercise every tool |
| infra | `terraform plan` shows **no changes** for all existing tenants after module edits; then plan/apply the new tenant dir |
| heli-plugin | `uv run python -m unittest discover -s plugins/helical-platform/tests`; skill + plugin validators; live `compute-embeddings` walkthrough |

## Explicitly deferred

Fine-tuning (stage 2) and perturbation/ISP tools (stage 3, **tentative** — and it needs a metering decision first, since the platform already meters ISP for enterprise through `ProjectTypeQuota.isp_credits_*`; see DESIGN §7.0). Both reuse this scaffolding + new `ModelPrice.operation` rows, free-credit launch sizing, tenant right-sizing (consumer-tier trim of JupyterHub/RDS/Airflow), dags-repo changes (none needed given deterministic tokens).

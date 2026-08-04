# Implementation Plan — Helical Platform Plugin (metered individual embeddings via MCP)

Companion to [`DESIGN.md`](DESIGN.md). Design decisions live there; this document sequences the work into milestones and tickets across four repos: `dashboard`, `infra`, `helical-mcp`, `heli-plugin` (plus k8s manifests in the `configs` repo).

Status: planned · Last reviewed: 2026-07-30 · Tracked as GitHub epic [helicalAI/dashboard#1756](https://github.com/helicalAI/dashboard/issues/1756)

## 0. Decisions and facts this plan is built on

Decisions (from DESIGN.md, confirmed by the product owner):

- Full program, phased; each milestone independently landable and flag-gated.
- **Token accounting is deterministic**: each row/cell costs a fixed number of tokens per model (data-prep + tokenizer dependent). Total = per-row factor × row count (× epochs for fine-tuning, × genes-to-perturb for perturbation, later). Estimate ≡ actual, so **billing needs no dags-repo change** and the debit can happen at launch.
- Prepaid credits; Stripe-seeded top-up behind a provider interface; free monthly grant supported in schema, enablement is a launch parameter.
- Confirmation is prompt-level (skill instructs the agent); no server-side `PendingConfirmation` on the MCP path. Mitigations: balance ceiling, per-user concurrent-run cap, estimate echoed at kickoff.

Load-bearing repo facts (verified 2026-07-27):

| Fact | Consequence |
|---|---|
| `helical-mcp` targets `/api/mcp/trpc/*`, deleted from the dashboard in `233f4aa8` (2026-07-01). Every tool 404s. | Keep it as transport (FastMCP/`mcp` 1.26, Streamable HTTP, `ForwardSessionMiddleware`); rebuild every tool against a new surface. |
| Live dashboard pattern: `src/app/api/agentcore-mcp/` — `defineGetTool`/`definePostTool` + Zod + `authenticateUser` bearer auth + singleton OpenAPI registry. | Reuse the lib with an injected registry for a new, separate route group so the enterprise chat gateway never sees the new tools. |
| `_helpers.ts` exports **unused** `triggerValidated(input, user, projectSlug)` → `airflowServices.triggerDagRun` (validation, `output_dir` stamping, `assertTriggerProjectAccess`, `RunMeta` write inside). | The direct-launch path already exists; `startEmbeddingRun` wraps it. |
| `auth.syncUser` → `addUserToDefaultProject` (`src/lib/auth-db.ts`) is the single login-time membership hook (currently joins hard-coded `pilot`). | Natural seam for per-user project auto-provisioning. |
| `recordDagRunMeta` silently skips the `RunMeta` write unless the caller holds **EDITOR** membership. | Auto-provisioned membership must be EDITOR or billing/lineage silently breaks. |
| **No presigned URLs exist** (uploads proxy bytes through `/api/s3/multipart`; downloads limited to `.pdf`/`.csv`). `@aws-sdk/s3-request-presigner` not a dependency. | Presigned PUT + GET endpoints are net-new. |
| `dataSc.create` copies staged files via `fs.copyFile` on the S3 fuse mount (`src/lib/relocateDatasetFile.ts`). | Needs an S3 `CopyObject` branch for mountless deployments (switch exists: `storageBackend.useLocalFiles`). |
| The **dags repo writes `terminal_state`/`execution_time` directly to Postgres**; dashboard self-heals via `reconcileActiveRuns` on read. No webhook. | Billing settlement must be lazy (on read) + a sweep job; cannot rely on a dashboard-side completion callback. |
| `src/lib/cognito-bearer-auth.ts:37` verifies with `clientId: null` (any client in pool; acknowledged TODO). | Must be tightened to an env-driven allowlist + scope check before exposure. |
| Infra tenant = 4 files copied from `envs/stage/qa-tenant/` + gitignored tfvars (23 secrets/URLs). Pool has **no lambda triggers**. Cognito attribute `required` flags are immutable (change ⇒ pool replacement). | Self-signup + attribute relaxation must be correct at tenant birth; provisioning is lazy in-dashboard, not a Cognito trigger. |
| `envs/dev/helical-mcp-cpl/` builds branch `develop` of `helicalAI/helical-mcp` — **which does not exist**; no `buildspec.yml`, no ECR repo in TF, no k8s manifests anywhere. | helical-mcp deployment is greenfield. |
| Browser services' ALB ingress uses `alb.ingress.kubernetes.io/auth-type: cognito`. | The MCP ingress must NOT copy those annotations (bearer-only, non-browser clients). |

---

## Milestone 0 — Foundations (added 2026-07-30; land before M1)

Two design sections were added after this plan was first written — §2.4 (extend the platform rather than build a lean service around the DAGs) and §2.5 (parametrising what each edition exposes). Both produce prerequisite work.

### 0.1 Shared DAG-contract package — #1831
Extract from the dashboard, ~450 lines of plain TypeScript: `constants/paths.ts` (drop its one type-only `Modality` import), `lib/models/classify.ts` verbatim, the DAG-contract half of `airflow-constants.ts` (split from its `lucide-react` icon map), and `embeddingsApiPayloadSchema` (relocate out of `src/app/agents/`). The `conf` contract is documented nowhere in the dags repo, so any second caller re-derives and drifts from it.

### 0.2 Tenant capability manifest — #1832
`src/config/tenants.ts` keyed by `NEXT_PUBLIC_NAMESPACE`, mapping each deployment to `edition: "b2b" | "b2c"` plus explicit per-tenant overrides. Fail at boot on an unknown namespace (the repo has no env validation today). Absorb the eight existing ad-hoc namespace checks, including the tenant array duplicated between `PlatformContext.tsx:154` and `airflow-services.ts:185`.

### 0.3 Edition enforcement — #1833
Root-level tRPC path allowlist (deny-by-default, `NOT_FOUND` for unclaimed routers) + `featureProcedure(capability)` for finer gates; widened `middleware.ts` matcher with a manifest-driven page-prefix check; `Sidebar.tsx` refactored to a data-driven `NAV_ITEMS` array. Do **not** prune the root router object — `AppRouter` is the client's type source. Jupyter/Coder and cross-indication porting become absent capabilities rather than bespoke conditionals. CI: capability completeness, "every route claimed by an edition", per-edition snapshots.

### 0.4 B2C edition surface — #1834
`src/app/(b2c)/` route group for auth, sign-up, and checkout; `hideSignUp={!features.selfServeSignup}` in `authenticator.tsx`; scheduled reconciliation of the resolved sign-up capability against the pool's `admin_create_user_config`. Verify the `pilot`-project EDITOR landmine is absent in production.

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
| **Estimate an embedding run** | `POST /airflow/estimate/embedding` **(proposed)** | ❌ | **New** — same body as the trigger, returns tokens, price, assumptions, `quote_id` | #1762, #1766 |
| **Estimate a fine-tuning run** | `POST /airflow/estimate/finetuning` **(proposed)** | ❌ | **New** — separate endpoint; epochs multiply the count, so the formula and inputs differ | #1766 |
| *(Estimate a perturbation run)* | `POST /airflow/estimate/perturbation` | ❌ | Roadmap stage 3 (§7.0), priced by genes perturbed | later |

### Runs

| Capability | Target path | Today | Work | Ticket |
|---|---|---|---|---|
| Compute embeddings | `POST /airflow/trigger/embedding` | ✅ `…/embedding/{conversationId}` | Port — drop the scope segment, execute directly instead of enqueuing, require `quote_id` | #1762 |
| Fine-tune | `POST /airflow/trigger/finetuning` | ✅ `…/finetuning/{conversationId}` | Port — same three changes | #1762 |
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
- **`start_*` requires the `quote_id` its estimate returned**, and the estimate body is built by the same code as the trigger body, so a quote necessarily prices exactly what will run.

Two things this audit surfaced that were not previously tracked:

- **Model rename** (#1840) exists only as a tRPC procedure and is keyed on `mlflowRunId` while the rest of the surface uses `model_id`. Exposing it means resolving one to the other, or accepting an inconsistent identifier in one tool.
- **Model upload** (#1841) has no precedent anywhere in the platform — MLflow is populated exclusively by fine-tuning runs. This is not an endpoint, it is a feature: validating an uploaded artifact, registering it in MLflow, deciding what a user-supplied model may be used for, and pricing it, since §5.1 prices per token by *model coefficient* and an unknown model has none. **It needs a scope decision before it is estimated** — it may well not belong in the first release.

## Milestone 1 — Dashboard: subject-scoped MCP route group + user provisioning

Everything gated by env `INDIVIDUAL_TENANT=true`; flag off ⇒ zero behavior change.

### 1.1 Generalize the tool-definition lib
- Extract `src/app/api/agentcore-mcp/_lib/define-tool.ts` + `_lib/registry.ts` so the `OpenAPIRegistry` is injected (e.g. `makeDefineTool(registry)`) instead of imported as a singleton. `agentcore-mcp` behavior stays byte-identical (its spec, its S3-published OpenAPI, its tests).
- New group `src/app/api/platform-mcp/` with its own `_lib/` (own registry), own `openapi.json` route (bearer-gated), own README documenting conventions.
- Port the registration-drift test pattern (`tests/agentcore-mcp/route-registration-drift.test.ts`) for the new registry.

### 1.2 Bearer-auth hardening
- `src/lib/cognito-bearer-auth.ts`: replace `clientId: null` with allowlist from `COGNITO_ALLOWED_CLIENT_IDS` (comma-separated) and require scope `COGNITO_REQUIRED_SCOPE` when set. Absent env ⇒ current behavior (existing tenants unaffected).
- Tests: wrong-client, wrong-scope, and legacy-mode acceptance.

### 1.3 Individual project auto-provisioning
- `src/lib/individual-project.ts`: `resolveIndividualProject(prisma, user)` — transactional upsert of `Project { slug: "user-<cognitoSub>", name }` (satisfies the existing slug regex `^[a-z0-9]+(?:-[a-z0-9]+)*$`; slug immutable — it is a security principal in S3 paths and MLflow workspace names) + `ProjectMembership { role: EDITOR }`.
- Wire into (a) `addUserToDefaultProject` (replaces the `pilot` join when `INDIVIDUAL_TENANT=true`), and (b) `requireSubjectProject(user)` — the per-request resolver every `platform-mcp` handler calls, so first authenticated MCP request provisions (DESIGN §4.4).
- **Also provision the user's MLflow workspace** (#1835): workspaces are created by hand in the MLflow UI today, and a user without the one `workspaceForProjectSlug(slug)` resolves to cannot complete a run — the DAG operator refuses to start without `conf.metadata.project` precisely because it scopes that workspace. Idempotent, same concurrency guarantees as the project upsert.
- Membership re-check per DESIGN §2.3: the resolver + in-service checks, not just route-edge.
- Tests: idempotency under concurrent first requests; slug shape; EDITOR role present; flag-off ⇒ untouched.

### 1.4 Models + datasets tools
- `listModels` — wraps `listModels` (`src/trpc/routers/models/list.ts`), promoted default, family filter, joined with `ModelPrice` (M2) to include `price_per_million_tokens`.
- `createDatasetUpload` — net-new presigned PUT (add `@aws-sdk/s3-request-presigner`): short-lived URL under `datasetUploadPrefix(slug, username)`; extension validated against `ALLOWED_DATA_EXTS`, size cap.
- `registerDataset` — lift `dataSc.analyzeUpload` + `dataSc.create` procedure bodies into a shared service (they are inline today); add an S3 `CopyObject` branch to `src/lib/relocateDatasetFile.ts` behind `storageBackend.useLocalFiles`. Returns dataset with `cellCount`/`geneCount` (estimator inputs).
- `listDatasets` / `getDataset` — port of `agentcore-mcp/data/` handlers, subject-project-scoped.

### 1.5 Embedding-run tools
- `estimateEmbeddingRun` — calls M2 estimator; returns tokens, price, assumptions, `quote_id`, expiry.
- `startEmbeddingRun` — `requireSubjectProject` → M2 `debitForRun` (atomic) → `triggerValidated(...)` with `investigationId: false`, `dagRunId = generateDagRunId("plugin")`; enforces `MAX_CONCURRENT_RUNS_PER_USER`; response echoes the estimate. No `PendingConfirmation`.
- `listEmbeddingRuns` / `getRunStatus` — `runs/list.ts` + `pollActiveStatus`; `getRunStatus` invokes M2 `settleRun` when it observes a terminal state; terminal responses include tokens + charge.

### 1.6 Results tools
- `listResults` / `searchResults` — `ArtifactMeta` rows joined to the subject's runs; filters: model, dataset, date, free-text on `displayName`.
- `downloadResult` — net-new presigned GET for `ArtifactMeta.s3Key` via `mountPathToBucketKey` + `getS3Client`/`getTenantBucket` (`src/lib/s3Utils.ts`); short-lived, subject-bound.

### 1.7 Isolation test suite
- Cross-user denial for every tool (unauthorized ≡ nonexistent in status/shape/wording); `projectScopeRule` ESLint clean; `docs/DATA_SEPARATION.md` checklist walked for each new data-touching path.

## Milestone 2 — Dashboard: billing schema + metering services

Prisma per `prisma/README.md` rules: inline model comments, ERD + Ownership-boundaries table updated in the same change, hand-authored migration names, read-only `/dev/<table>` routes.

### 2.1 Schema
- `ModelPrice` — `{ id, model, version?, operation (embedding|finetuning|perturbation), tokensPerRow Decimal, priceMicroUsdPerMToken Int, active, effectiveAt }`; seeded by migration.
- `CreditLedger` — append-only: `{ id, userId, entryType (topup|grant|debit|refund|expiry), creditClass (paid|free), amountMicroUsd Int (signed), tokens Int?, runMetaId? , quoteId?, periodKey?, providerRef?, createdAt }`. Partial unique indexes: `(runMetaId, entryType)` for debit/refund idempotency; `(userId, periodKey, entryType)` for once-per-period grants; unique `providerRef` for webhook idempotency (enforced in migration SQL, documented in README prose).
- `EstimateQuote` — `{ id, userId, model, modelVersion?, datasetId, operation, tokens, priceMicroUsdPerMToken, totalMicroUsd, assumptions Json, expiresAt }`.

### 2.2 Services (`src/lib/billing/`)
- `computeTokens(operation, model, dataset)` = `cellCount × tokensPerRow` (embeddings; formula gains epoch/gene factors for later operations).
- `createEstimate(user, input)` → quote row (inputs already on `DataScMeta` from `analyzeUpload`).
- `debitForRun(tx, user, quote, runMetaId)` — charge at launch: row-locked balance read + debit insert in one transaction; free class consumed before paid; honors unexpired quote pricing; typed `insufficient_balance` error (shortfall + top-up URL) mapped to the DESIGN §6 shape.
- `settleRun(runMetaId)` — idempotent; refund entry on terminal `failed`/`cancelled` (charge only completed work). Called from `getRunStatus`/`reconcileActiveRuns` read paths + a sweep script (`scripts/`, cron-able) because terminal state is written by the dags repo directly.
- `grantMonthlyFreeCredit(user)` — no-op unless `FREE_MONTHLY_TOKENS` set; lazily invoked from balance reads; `(userId, periodKey)` uniqueness = once per period; period rollover writes `expiry` entries for unspent free credit.
- `getBalance` / `getUsage` tools (ledger aggregation + existing `users/usage.ts` rollups).

### 2.3 Tests
Debit/refund idempotency (incl. DAG retries), concurrent-kickoff no-overdraft race, free-before-paid ordering, grant non-accumulation, quote expiry, quoted-price honored across a price-table change.

## Milestone 3 — helical-mcp: rebuild against the new surface

### 3.1 Client + error hygiene
- `src/helical_mcp/clients.py`: remove dead tRPC-proxy helpers; add `dashboard_plugin_get/post(path, ...)` targeting `/api/platform-mcp/*`; forward `Authorization` only (drop cookie forwarding on this surface); keep `forwarded_headers` contextvar + `ForwardSessionMiddleware`.
- Catch `httpx` errors / dashboard error envelopes and re-raise sanitized messages (no URLs, no internals) — FastMCP surfaces exception text verbatim as the `isError` result.

### 3.2 Tool modules
- `tools/{models,datasets,embeddings,results,account}.py`, one `async def tool_*` per DESIGN §7 tool; register in `server.py` (two-file convention); delete stale modules and README tool table rows.
- Docstrings are the LLM contract: `tool_start_embedding_run` states "present the estimate and obtain the user's explicit confirmation before calling this" (mirrors the skill); cross-reference tools per house style.

### 3.3 Protocol + ops hardening
- Mount next to `mcp.streamable_http_app()`: `GET /.well-known/oauth-protected-resource` (issuer/authz-server metadata from `COGNITO_ISSUER`, `MCP_RESOURCE_URL`), `GET /health`; `401` + `WWW-Authenticate` challenge when `Authorization` is absent.
- Declare `uvicorn` as a real dependency (currently transitive); Dockerfile: non-root user, `HEALTHCHECK`, pinned `uv` base image.

### 3.4 Tests
respx suites per tool group against mocked `/api/platform-mcp/*`; ASGI middleware test (currently untested); well-known/health tests.

## Milestone 4 — Infra: individual tenant, Cognito, helical-mcp deployment

### 4.1 Self-signup module change
- New `allow_self_signup` (bool, default false, modeled on `enable_sso`).
- `user_pool.tf:90` → `allow_admin_create_user_only = !(var.enable_sso || var.allow_self_signup)`; `given_name`/`family_name`/`phone_number` `required = var.enable_sso || var.allow_self_signup ? false : true`.
- Regression gate: `terraform plan` renders **no changes** for every existing tenant (both expressions are identity-preserving for `(enable_sso, allow_self_signup=false)`).

### 4.2 MCP app client
- `modules/tenant/user_pool_client_mcp.tf`, count-gated on new `enable_mcp_client`: public (`generate_secret = null`), `allowed_oauth_flows = ["code"]`, `explicit_auth_flows = [ALLOW_REFRESH_TOKEN_AUTH, ALLOW_USER_AUTH, ALLOW_USER_SRP_AUTH]`, scopes `["openid","email","${aws_cognito_resource_server.agentcore.identifier}/invoke"]`, `refresh_token_validity = 30` (days), callbacks from new `mcp_client_callback_urls` (pinned loopback `http://127.0.0.1:<port>/callback` + hosted callback; Cognito has no wildcard ports).
- Publish client id to SSM per the `api`/`m2m` convention.

### 4.3 Individual tenant env dir
- `envs/dev/individual-tenant/` (dev first): copy `envs/stage/qa-tenant/{main.tf,variables.tf,data.tf,outputs.tf}`; fix `data.tf` remote-state paths (dev eks, region globals); set `namespace`, `cognito_auth_sudomian` (expect the `amazoncognito.com` fallback if the 4-per-region custom-domain cap is hit), `allow_self_signup = true`, `enable_mcp_client = true`; gitignored tfvars (23 secrets/URLs).
- Dashboard env for this tenant (configs repo `application.yaml`): `INDIVIDUAL_TENANT=true`, `COGNITO_ALLOWED_CLIENT_IDS`, `COGNITO_REQUIRED_SCOPE`, optional `FREE_MONTHLY_TOKENS`, `MAX_CONCURRENT_RUNS_PER_USER`.

### 4.4 helical-mcp build + deploy (greenfield)
- helical-mcp repo: add `buildspec.yml`; align `helical-mcp-cpl` source branch with reality (point at `main` or create `develop`).
- Ensure ECR repo `mcp` exists (add to globals ECR TF if unmanaged).
- configs repo: `envs/dev/mcp/application.yaml` — Deployment + Service + ALB Ingress (host e.g. `mcp-individual.helical-ai.bio`), **without** browser `auth-type: cognito` annotations; `DASHBOARD_URL` → the tenant dashboard. infra repo: `manifests/argocd/` root-app entry.

## Milestone 4b — GPU access strategy (AWS vs Nebius vs managed) — #1837

Per DESIGN §5.4. Not an ops detail: per-token pricing means we absorb all compute-cost variance, and three consequences reach billing correctness.

- **Spot vs on-demand for the consumer namespace.** Spot/on-demand is selected by *tenant namespace name* (`novartis`/`mt`/`pfizer` get on-demand, everything else spot), so a new individual tenant lands on spot — and the embedding DAG configures **no retries**. An interruption becomes a failed run, which per §5.1 we refund, so we pay for the GPU and collect nothing. Decide: add the namespace to on-demand, or accept spot and add a retry policy. The two provider lists have already drifted (embedding's omits `dev`, fine-tuning's includes it) — reconcile.
- **Pin the image the coefficients were measured against.** AWS and Nebius pin different bio-agent image tags; the per-row token coefficient is a property of the model's data prep and tokenizer, so it is not automatically valid across images. Re-verify on any provider or tag change.
- **Artifact path prefix differs by provider** (`/datasets/` on the Nebius return path vs `/projects/` on AWS) — `download_result` and artifact indexing handle both, or the tenant pins one provider.
- **Remove `node_type`/`num_devices` from `start_embedding_run`** (M1.5): the platform picks the compute profile the coefficient was priced against, otherwise a caller can multiply our cost at a fixed price.
- **Evaluate the managed options** against measured GPU-hours, not list prices: **Modal** is the designated escape hatch (per-second, scale-to-zero, and the embedding compute is a single containerised CLI so only one task moves); **Baseten** is serving-oriented and a partial fit; **Runware** was evaluated and ruled out — it is a generative-media inference API whose bring-your-own-weights is limited to diffusion artifacts, with no arbitrary containers or batch GPU compute, so it cannot run the bio-agent image at all.

## Milestone 5 — Dashboard: payment processor selection + Stripe-seeded top-up

- **Select the processor** (#1838). Stripe is the seed, not a decision; the choice gates the integration but not the ledger, which is processor-agnostic by construction. Decide before this milestone starts.
- `src/lib/billing/provider.ts` interface: `createTopUpSession(user, amountMicroUsd)`, `verifyWebhookEvent(req)`, `refund(providerRef)`; Stripe implementation (Checkout session bound to `userId`).
- The top-up page is Helical-owned on our domain; the provider's hosted checkout collects card details. We never render a payment form, and the MCP surface only ever hands out a short-lived URL (DESIGN §5.3).
- `POST /api/billing/webhook`: signature + freshness verification; `checkout.session.completed` → `topup` ledger entry, idempotent on `providerRef`. Success/cancel pages.
- `insufficient_balance` responses embed the short-lived session URL.

## Milestone 6 — heli-plugin artifacts

- `.mcp.json`: remote Streamable HTTP entry for deployed helical-mcp (STDIO scaffold retained for local dev).
- **Done ahead of the milestone**: the legacy article skills are removed and replaced by `skills/compute-embeddings/` and `skills/fine-tune-model/` (DESIGN §8.3), and `mcp/server.py` now implements the §7 tool contract for both — sixteen tools, with the estimate-before-spend split and the rejection of `node_type`/`num_devices`/`device`/`output_dir` asserted by tests. Remaining here: point `.mcp.json` at the deployed helical-mcp URL for production.
- Settle the three open publication items in `plugin.json`, all decisions rather than edits: the **licence** (no LICENSE file ships today, so terms default to all-rights-reserved), the **privacy policy and terms URLs** (removed rather than guessed at — real paths needed), and whether **`capabilities: ["Read"]`** is accurate for a plugin that starts billable runs. Publisher, support and repository metadata are done.

## Milestone 6c — Local execution skill (#1844)

Per DESIGN §7.2. A third skill, `run-helical-locally`, driving the open-source package (`pip install helical`, AGPL-3.0) on the user's own GPU through the agent host's shell and code execution. **No MCP tool, no route, no dashboard ticket** — the plugin's server cannot execute on the user's machine, so this is instructions only and declares no tool dependency.

- Preflight before install: Python **≥ 3.12 < 3.13** exactly, `nvidia-smi`, compute capability ≥ 8.9 for Evo 2, disk for a multi-GB torch/CUDA install. Caduceus is a hard blocker on CPU (`mamba_ssm` is CUDA-only).
- Extras per model: `helical[mamba-ssm]` for Caduceus/Mamba2, `helical[evo-2]` for Evo 2, `flash-attn --no-build-isolation` where used.
- Reaches four models the platform does not host — **Tahoe-X1, Caduceus, Evo 2, GenePT** — and therefore the ten reference prompts (§12.2) that have no hosted model.
- Same honesty rules as the hosted skills, plus two of its own: recommend a backend explicitly with a reason, and never compare a local result against a hosted one as a model comparison (different builds).
- Independent of everything else here: no port, no billing, no tenant, so it can land at any time.

## Milestone 6b — Launch readiness (#1836)

Two distribution blockers with no owner. **No customer-facing support or ticketing channel exists**, which makes the "contact support" branch of the §6 error table a dead end; a dedicated public issue tracker is the cheapest credible option. And the retention/deletion policy must be written to match reality — the account lifecycle already soft-deletes after 90 days of inactivity, so the policy has to state what that does to project contents, artifacts, and **unspent credit**, which is a refund question as well as a data question.

## Milestone 7 — End-to-end verification & dogfood

Acceptance run on `individual-tenant` (dev): hosted-UI self-signup → PKCE link from Claude Code/Codex → upload `.h5ad` → register → estimate → confirm → embedding DAG runs → status shows tokens + charge → artifact downloads → ledger/balance consistent. Second account proves isolation (all reads on user A's ids → identical 404 shape). DESIGN §12 is the test checklist; the confirmation-skipping-client scenario validates §6.1 mitigations.

---

## Ordering & parallelism

- **M0 first.** 0.1 unblocks anything touching the DAG contract; 0.2–0.4 unblock the edition split.
- M2 schema → M1 routes (one dashboard PR train behind the edition manifest).
- M4.1–4.2 (Cognito module changes, default-off) can land any time, subject to the two pairing constraints in M0.
- M3 starts once M1 contracts exist (develops against `npm run dev`).
- M4.3–4.4, M5, M6, M6b follow; M7 last.
- **M4b must be settled before the first paying user** — the spot default silently costs money on every interrupted run. The processor selection in M5 must be settled before M5 starts.

## Per-repo verification

| Repo | Gate |
|---|---|
| dashboard | `npm run typecheck && npm run lint && npm run test` (incl. new `tests/platform-mcp/*`; `projectScopeRule` clean); `npx prisma migrate dev` clean; `/dev/<table>` routes render |
| helical-mcp | `uv run pytest`; local `MCP_TRANSPORT=streamable-http uv run helical-mcp` + `claude mcp add --transport http` with a dev bearer; exercise every tool |
| infra | `terraform plan` shows **no changes** for all existing tenants after module edits; then plan/apply the new tenant dir |
| heli-plugin | `uv run python -m unittest discover -s plugins/helical-platform/tests`; skill + plugin validators; live `compute-embeddings` walkthrough |

## Explicitly deferred

Fine-tuning and perturbation tools (DESIGN §7.0 stages 2–3; reuse this scaffolding + new `ModelPrice.operation` rows), final payment-processor decision, free-credit launch sizing, tenant right-sizing (consumer-tier trim of JupyterHub/RDS/Airflow), dags-repo changes (none needed given deterministic tokens).

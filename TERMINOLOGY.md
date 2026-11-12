# Shared terminology and naming conventions

**Status:** Cross-repository convention for the extracted libraries. This document defines preferred terms for new APIs and documentation. It does not override a repository's versioned wire schema, persisted schema, or domain-specific contract.

## 1. Core workflow vocabulary

| Term | Canonical meaning | Naming |
|---|---|---|
| Workflow | A bounded business process containing tasks and governed by one workflow identity. | workflow_id |
| Task | A bounded unit of work within a workflow. | task_id |
| Attempt | One numbered execution or delivery try within its owning scope. Attempt numbers are positive and normally start at 1. | attempt; use a scope-specific name such as delivery_attempt when needed |
| Attempt identity | Stable identity for one particular attempt, distinct from its ordinal. | attempt_id |
| Execution | A host/runtime execution aggregate. Do not use it as a synonym for a provider run or OS process. | execution_id when an identity is needed |
| Provider run | A run created and identified by an external provider. | provider_run_id |
| Worker | A logical execution actor. It is not an OS process. | worker_id |
| Job | Legacy source or wire vocabulary. New generic contracts use workflow. | Preserve legacy job_id only where compatibility requires it. |

The COS-AP v1 envelope's job_id and Node-compatibility structures' job_id are deliberate compatibility names. Do not silently rename them to workflow_id; map them explicitly at an adapter boundary.

## 2. Identity and actor vocabulary

| Term | Meaning |
|---|---|
| Principal | Caller identity asserted by an already-authenticated host or transport. A value object does not authenticate its contents. |
| Subject | The identity or entity named by a claim, attestation, approval, or evidence statement. |
| Actor | The party that performed or requested an action. Use a specific identity field where possible. |
| Producer | The party or service that declares it created an artifact or evidence item. Producer metadata is a claim until authenticated. |
| Issuer | The party that issues a signed statement or token. |
| Audience | The intended recipient or service boundary for a statement. |
| Sender / recipient | Message-routing participants. Use endpoint-specific identifiers when routing to mailbox endpoints. |
| Reviewer | An identity participating in a review decision under an explicit policy. |

These roles are not interchangeable. A principal may act as a producer, but that relationship must be supplied and verified by the owning host.

## 3. Data, evidence, and authority vocabulary

| Term | Canonical meaning |
|---|---|
| Artifact | Exact bytes plus a content identity. |
| Artifact reference | Typed locator/binding for an artifact; not the artifact bytes themselves. |
| Manifest | Immutable, canonical grouping of artifact references and lineage claims. |
| Evidence | Material or an observation offered to support a proposition or decision. Evidence may reference artifacts. |
| Observation | A time-bounded report from a named observer/source. It is not, by itself, proof of truth or authority. |
| Verification | Evaluation of evidence against explicit rules and trusted inputs. |
| Attestation | An assertion bound to an issuer and usually a signature. A parsed attestation is not automatically trusted. |
| Receipt | A record of a request, delivery, or decision. Its authority depends on authentication, binding, and custody checks. |
| Policy | Versioned rules and constraints supplied to an evaluator. A policy value is not itself authorization. |
| Decision | The result returned by an authorized policy evaluator. |
| Authorization | An authority decision enforced by the owning host. Structural validation, a digest, or a local capability-shaped value is not authorization. |
| Capability | A scoped token/value representing permission issued by an authority. Its validity depends on the issuer and enforcement boundary. |

A digest proves content identity under its algorithm and serialization rules. It does not prove authorship, truth, approval, custody, or authorization.

## 4. Lifecycle and result vocabulary

Prefer these meanings for new types and fields:

- state: lifecycle position of a durable entity or aggregate.
- status: reported condition of an external resource, service, or compatibility record. Do not use it as a vague synonym for every result.
- outcome: observed result of an attempted operation (for example, accepted, rejected, not sent, or unknown).
- decision: policy or governance result (for example, approve, reject, or abstain).
- disposition: classification of an observation or lookup result (for example, available, not found, or unavailable).
- classification / class: category used to group failures or evidence.
- reason_code: stable machine-readable cause for a negative/uncertain outcome.
- code / error_code: stable machine-readable error identifier.
- reason: concise explanation; callers must not parse it as a stable identifier.
- message: human-readable diagnostic or message body.

Existing `ReviewerStatus`, `policy_hash`, and other versioned/persisted names are compatibility exceptions; keep their semantics documented rather than changing their wire or storage identity in place.

## 5. Identifier, digest, version, and key conventions

- `<entity>_id`: opaque identifier for an entity. Treat as opaque unless that entity's contract defines a grammar.
- `<entity>_ref`: typed reference/locator containing more than a bare identifier.
- idempotency_key: caller-supplied stable key used to identify one logical side effect across retries. It is not a request ID or correlation ID.
- request_id: identity of one request.
- message_id: identity of one message.
- correlation_id: groups related requests/messages; it does not deduplicate them.
- causation_id: identifies the immediate event/request that caused another event, when the protocol models causation explicitly.
- `*_sha256`: exact SHA-256 digest/fingerprint, represented in the format required by that contract (normally lowercase hexadecimal).
- `*_digest`: algorithm-neutral digest; pair it with an explicit algorithm identifier.
- `*_hash`: retain only where required by a versioned wire field, database column, or established compatibility contract. Document the actual algorithm. For new names, prefer *_sha256 when the algorithm is fixed to SHA-256.
- `*_fingerprint`: canonical fingerprint/pseudonym with its own stated derivation. Do not assume it is interchangeable with an entity ID or public-key ID.
- schema_version: version of a serialized schema.
- `<entity>_version`: version of a policy, definition, or entity contract.
- `<entity>_revision`: monotonic revision of an evolving aggregate/snapshot.
- key_id: identifier of a cryptographic key; not the key material or fingerprint.

## 6. Time and quantity conventions

- `*_at`: absolute timestamp. New cross-repository contracts should use RFC 3339 UTC with explicit Z, unless a specific protocol requires another exact representation.
- `*_at_ms`: Unix epoch timestamp in milliseconds. Never compare or substitute it directly for an RFC 3339 string without conversion.
- `*_unix_ms`: explicitly states Unix epoch milliseconds; retain in existing public structures.
- `*_seconds`, `*_ms`, `*_bytes`, `*_count`: include units in quantity names.
- `*_index`: position; state whether it is zero-based or one-based in the contract. Internal collection indices are zero-based by default.
- *_count: number of elements, not the final index.
- Use plural nouns for collections and *_by_<key> for maps indexed by a key.

## 7. Language-level naming

- Rust: UpperCamelCase types/traits, snake_case modules/functions/fields/locals, SCREAMING_SNAKE_CASE constants.
- JavaScript/TypeScript: camelCase functions/locals and PascalCase types; serialized field names stay contract-defined.
- Python: snake_case functions/locals/fields and PascalCase types.
- Prefer role-specific names (`workflow_id`, `provider_run_id`, `duration_weights`) over bare id, value, data, count, status, or result when a name crosses a function or contract boundary.
- Boolean names should communicate the predicate: `is_*`, `has_*`, `can_*`, or `requires_*` where natural.
- Function names: `parse_*` parses representation, `validate_*` checks structural/domain constraints, and `verify_*` evaluates proof/authenticity against explicit trusted inputs. Do not call parsing or digest comparison verification when no trust authority is involved.

## 8. Repository and package naming

- Repository and package slugs use lowercase kebab-case; Rust import paths use the corresponding snake_case crate name.
- Prefer a capability-oriented name: domain or platform qualifier plus the bounded capability (for example, linux-project-quota or weighted-shard-planner). Avoid embedding a parent application's internal module path in a reusable package name.
- Retain a source-ecosystem prefix such as runalith- only while the extracted contract, persisted identifiers, or public semantics remain specific to that ecosystem. Do not remove the prefix mechanically: a GitHub rename can leave database identifiers, protocol names, dependency URLs, and documented provenance inconsistent.
- New variable and field names should describe the entity/role and include units or scope where ambiguity is possible. Generic names such as value, data, id, count, and result are acceptable only inside a narrow local scope where the type and role are obvious.

## 9. Repository-specific vocabulary

| Repository | Preferred concept names and distinctions |
|---|---|
| workflow-domain | workflow_id, task_id, WorkflowPlan, TaskState, WorkflowState, TaskOutcome. node_* APIs are explicit legacy Node-compatibility projections. |
| protocol-contracts | Envelope, message_id, message_type, request_id, correlation_id, versioned wire contract. COS-AP v1 job_id, sender, and recipient remain exact wire names. |
| content-artifacts | ArtifactRefV1, ArtifactManifestV1, ArtifactSourceV1, ArtifactLineageV1, read observation, and disposition. A read observation is not an authorization decision. |
| linux-project-quota | `ProjectQuotaLimits { bytes, inodes }`, `ProjectQuotaStatus { project_id, bytes_hard_limit, inodes_hard_limit, bytes_used, inodes_used }`, and `ProjectQuotaController`. Filesystem project quota is distinct from cgroup CPU/memory quota. |
| provider-intent-ledger | `ProviderIntentRequest` describes the immutable dispatch request, `ProviderIntentRecord` is the durable record, and `ProviderIntentReservation` is the reservation result. attempt is the workflow/task attempt ordinal; idempotency_key deduplicates one logical provider effect; provider_run_id identifies the external run. Ledger state is not provider workflow state. |
| workflow-recovery-policy | `StaleWorkflowEvidence` is the input; `RecoveryDecision` is the policy result; `RecoveryBlocker` explains deferral. The library does not perform recovery, retry, replay, or terminalize a workflow. |
| durable-a2a-mailbox | `AuthenticatedPrincipal` is the host-asserted identity, `MailboxStore` owns mailbox persistence operations, and `MailboxLimits` bounds resource use. Prefer endpoint_id, sender_endpoint_id, recipient_endpoint_id, message_id, and idempotency_key. “A2A” denotes this mailbox feature, not full Agent2Agent protocol conformance. |
| adaptive-goal-graph | `Goal`, `Idea`, `Plan`, and `Execution` are graph-node concepts; `graph_revision` identifies a graph revision; checkpoint/custody values are external-authority contracts, not local authority. |
| runalith-github-actions-provider | provider_id, provider_run_id, immutable provider intent, dispatch outcome, and provider observation. A GitHub Actions run is not the internal workflow or task attempt. |
| runalith-linux-sandbox | `CommandRequest`, `CommandResult`, `process_id`, `workspace`, and scoped `WorkspaceWriteCapability`. Process-tree cleanup evidence does not prove remote provider work stopped. |
| runalith-trust | `principal`, reviewer `subject`, `issuer`, `audience`, `trust_root_sha256`, `policy_version`, and `decision`. Verification is not application authorization. Existing `policy_hash` fields/columns are SHA-256 values retained for v1/storage compatibility. |
| weighted-shard-planner | An item is a named check; a duration_weight is measured duration in seconds; shard_count is a total; internal shard indices are zero-based while parsed CLI index/count selection is one-based. |

## 10. Compatibility and change policy

Names in a public wire contract, signed payload, canonical JSON representation, database schema, event discriminator, environment variable, or documented public API are compatibility surfaces. Do not rename them in place merely to satisfy style consistency.

For a compatibility-sensitive name:
1. Keep the serialized/persisted identifier unchanged.
2. Use a clearer local variable, accessor, wrapper, or adapter name where safe.
3. For a new wire or storage contract, choose the canonical names above.
4. If an old contract must change, introduce a new schema/version and an explicit migration/translation path.
5. Add tests proving old representations remain byte-for-byte or behaviorally compatible where required.

This document standardizes new naming and explains legacy exceptions. It does not itself change protocol, database, authorization, or execution semantics.

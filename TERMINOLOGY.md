# Shared terminology and naming conventions

**Status:** Naming standard for this standalone package. The standard applies to new identifiers and documentation. It does not silently rewrite versioned wire fields, signed values, persisted keys, or public compatibility surfaces.

## 1. Workflow vocabulary

| Term | Meaning | Preferred name |
|---|---|---|
| Workflow | Bounded process containing declared tasks. | workflow_id |
| Task | Bounded unit of work in a workflow. | task_id |
| Attempt | Positive ordinal for one execution/delivery try within its owner scope. | attempt; use delivery_attempt when ambiguous |
| Attempt identity | Stable identity for a specific attempt, not its ordinal. | attempt_id |
| Execution | Host/runtime execution aggregate, not an OS process or provider run. | execution_id |
| Provider run | External run created by a provider. | provider_run_id |
| Worker | Logical execution actor, not an OS process. | worker_id |
| Job | Legacy compatibility term. New generic APIs prefer workflow. | Preserve job_id only for compatibility. |

Legacy job_id fields remain isolated at compatibility boundaries. New domain fields should use workflow_id.

## 2. Identity vocabulary

| Term | Meaning |
|---|---|
| Principal | Caller identity asserted by an already-authenticated host or transport. A value object does not authenticate its contents. |
| Subject | Identity or entity named by a claim, attestation, approval, or evidence statement. |
| Actor | Party that performed or requested an action. |
| Producer | Party or service declaring that it created an artifact or evidence item. This is a claim until authenticated. |
| Issuer | Party that issues a signed statement or token. |
| Audience | Intended recipient or service boundary. |
| Sender / recipient | Message-routing participants. Use endpoint-specific identifiers when applicable. |
| Reviewer | Identity participating in a decision under an explicit policy. |

These roles are not interchangeable. A principal may act as a producer, but the relationship must be established by the owning host.

## 3. Evidence and authority vocabulary

| Term | Meaning |
|---|---|
| Artifact | Exact bytes plus a content identity. |
| Artifact reference | Typed locator/binding for an artifact, not the bytes themselves. |
| Manifest | Canonical grouping of artifact references and lineage claims. |
| Evidence | Material or observation offered to support a proposition or decision. |
| Observation | Time-bounded report from a named observer/source; not proof of truth or authority by itself. |
| Verification | Evaluation of evidence against explicit rules and trusted inputs. |
| Attestation | Assertion bound to an issuer and usually a signature; parsing does not make it trusted. |
| Receipt | Record of a request, delivery, or decision; authority depends on authentication, binding, and custody. |
| Policy | Versioned rules supplied to an evaluator; policy data is not authorization. |
| Decision | Result returned by a policy evaluator with the required authority. |
| Authorization | Authority decision enforced by the owning host. Validation, hashing, and capability-shaped values do not create authorization. |
| Capability | Scoped permission value issued by an authority and enforced at its boundary. |

A digest establishes content identity under a specific algorithm and serialization rule. It does not establish authorship, truth, approval, custody, or authorization.

## 4. Lifecycle and result vocabulary

Prefer distinct meanings for new fields:

- state: lifecycle position of a durable entity or aggregate.
- status: reported condition of an external resource or service.
- outcome: observed result of an attempted operation.
- decision: policy/governance result.
- disposition: classification of an observation or lookup result.
- classification: category used to group evidence or failures.
- reason_code: stable machine-readable cause for a negative or uncertain outcome.
- code / error_code: stable machine-readable error identifier.
- reason: explanatory text; never parse it as a stable identifier.
- message: human-readable diagnostic or message body.

Do not use status, state, outcome, decision, and disposition as interchangeable synonyms.

## 5. Identifiers, digests, versions, and keys

- `<entity>_id`: opaque identifier; validate its grammar only when the owning contract defines one.
- `<entity>_ref`: typed reference/locator containing more than a bare identifier.
- `idempotency_key`: stable key for one logical side effect across retries; not a request ID or correlation ID.
- `request_id`: identity of one request.
- `message_id`: identity of one message.
- `correlation_id`: groups related messages; does not deduplicate them.
- `causation_id`: immediate causal event/request identifier when the contract models causation.
- `*_sha256`: fixed SHA-256 digest/fingerprint in the exact representation defined by the contract.
- `*_digest`: algorithm-neutral digest; specify the algorithm separately.
- `*_hash`: retain for existing versioned fields/storage columns; document its actual algorithm. New fixed-SHA-256 fields prefer `*_sha256`.
- `*_fingerprint`: derived pseudonym/fingerprint with a stated derivation, distinct from entity IDs and key IDs.
- `schema_version`: serialized schema version.
- `<entity>_version`: policy/definition version.
- `<entity>_revision`: monotonic aggregate/snapshot revision.
- `key_id`: key identifier, not key material or fingerprint.

## 6. Time and quantities

- `*_at`: absolute timestamp; new cross-boundary contracts prefer RFC 3339 UTC with explicit `Z`.
- `*_at_ms` / `*_unix_ms`: Unix epoch milliseconds. Never compare directly with RFC 3339 text.
- `*_seconds`, `*_ms`, `*_bytes`, `*_count`: include units in quantity names.
- `*_index`: position; explicitly state zero-based or one-based. Internal collection indices default to zero-based.
- `*_count`: number of entries, not final index.
- Use plural nouns for collections and `*_by_<key>` for maps indexed by a key.

## 7. Language conventions

- Rust: `UpperCamelCase` types/traits, `snake_case` modules/functions/fields/locals, `SCREAMING_SNAKE_CASE` constants.
- JavaScript/TypeScript: `camelCase` functions/locals and `PascalCase` types.
- Python: `snake_case` functions/locals/fields and `PascalCase` types.
- Prefer role-specific names (`workflow_id`, `provider_run_id`, `duration_weights`) over bare `id`, `value`, `data`, `count`, `status`, or `result` at API boundaries.
- Boolean names should state predicates: `is_*`, `has_*`, `can_*`, `requires_*`.
- `parse_*` parses representation; `validate_*` checks structural/domain rules; `verify_*` evaluates proof/authenticity against explicit trusted inputs.

## 8. Repository and package names

- Package/repository slugs use lowercase kebab-case; Rust crate identifiers use the corresponding snake_case form.
- Use a capability-oriented name: platform/domain qualifier plus bounded capability.
- Do not inherit a parent product/application name in a reusable package name.
- Avoid naming public packages after source-tree directories, internal teams, deployment units, or runtime services.
- The repository must be buildable from its own checkout. Dependencies copied under `vendor/` are internal source, not external repository dependencies.

## 9. Package-specific scope

This package owns SQLite mailbox endpoints, ACLs, message lifecycle, idempotency, and atomic replies. A2A here means mailbox messaging, not complete Agent2Agent protocol or transport conformance.

## 10. Compatibility and change policy

Public wire fields, signed payloads, canonical JSON, database schemas, event discriminators, environment variables, and exported APIs are compatibility surfaces. Do not rename them in place merely for style.

For a compatibility-sensitive name:
1. Keep serialized/persisted identifiers unchanged when existing consumers may rely on them.
2. Use clearer local names, accessors, wrappers, or adapters where safe.
3. Introduce a new schema/version for generic replacements of legacy wire or storage values.
4. Provide an explicit migration/translation path before retiring the old form.
5. Test exact legacy bytes/behavior and generic forms independently.

This document establishes naming rules; it does not authorize protocol, storage, trust, or execution changes by itself.

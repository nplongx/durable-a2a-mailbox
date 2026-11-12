# durable-a2a-mailbox

A small Python library for durable agent-to-agent (A2A) endpoint registration, ACL-controlled message delivery, inbox acknowledgement, and atomic replies using SQLite.

This is a **mailbox persistence component**, not an MCP server, network service, authentication provider, agent scheduler, or ChatGPT tab wake-up mechanism. Here, “A2A” names the source broker's agent-to-agent mailbox feature; this package does not claim full Agent2Agent protocol conformance (Agent Cards, task lifecycle, JSON-RPC endpoints, or streaming transport).

## Terminology

See [TERMINOLOGY.md](TERMINOLOGY.md) for shared identifier, lifecycle, evidence, time-unit, and compatibility conventions.

## Features

- Per-principal endpoint registration and retirement with retained tombstone history.
- Private-by-default legacy sender-fingerprint ACLs.
- Exact sender-endpoint ACLs, including compatibility tokens for the existing 64-hex ACL field.
- Durable queued/received/acked/expired message lifecycle.
- Per-sender idempotency keys, correlation IDs, bounded body size, TTL, and inbox capacity limits.
- Paginated inbox reads that atomically move queued messages to received.
- Replies routed to the original sender, preserving correlation ID and acknowledging the original message in the same SQLite transaction.
- Explicit SQLite database path. The package does not open or migrate another application's database implicitly.

## Install

```sh
python -m pip install .
```

Runtime uses Python standard library only; no MCP, Starlette, HTTP, or ContextForge package is required.

## Quick start

```python
from pathlib import Path
from durable_a2a_mailbox import AuthenticatedPrincipal, MailboxStore

# The host must authenticate the user before constructing this value.
alice = AuthenticatedPrincipal(subject="alice@example.test")
bob = AuthenticatedPrincipal(subject="bob@example.test")

# Use a dedicated mailbox database, never the database owned by another service.
store = MailboxStore(
    Path("/var/lib/my-service/a2a-mailbox.sqlite3"),
    identity_secret=load_secret_from_secret_manager(),  # at least 32 bytes
)

alice_endpoint = store.register_endpoint(alice, "worker-a")
bob_endpoint = store.register_endpoint(bob, "worker-b")

# Choose exact endpoint-level ACL when multiple agents/workstreams share an identity.
store.set_peer_acl(bob, bob_endpoint["endpoint_id"], [alice_endpoint["endpoint_id"]])

message = store.send_message(
    alice,
    alice_endpoint["endpoint_id"],
    bob_endpoint["endpoint_id"],
    correlation_id="job-123",
    idempotency_key="job-123-attempt-1",
    message="Please inspect task 123.",
)
inbox = store.read_inbox(bob, bob_endpoint["endpoint_id"])
ack = store.ack_message(bob, bob_endpoint["endpoint_id"], message["message_id"])
```

`load_secret_from_secret_manager()` is an application placeholder, not a function provided by this package. Do not hard-code the secret or commit it.

## Authentication and tenant boundary

`AuthenticatedPrincipal` represents a **trusted identity assertion from the host**. The library does not verify HTTP headers, JWTs, OAuth tokens, HMAC claim headers, or user sessions. Only construct it after host authentication succeeds. A plain dictionary or a raw request is rejected.

The database is also an isolation boundary. Principals sharing one database can discover the active endpoint directory and potentially address endpoints when authorized by ACLs. Use a separate database per tenant/security domain when cross-tenant discovery is not intended. Directory labels are owner-supplied metadata and must not be treated as trusted identity claims.

The identity secret must be at least 32 bytes, shared by all instances that need consistent fingerprints/tokens for the same mailbox database, and stored outside the database. The library derives stable HMAC-SHA256 principal fingerprints and endpoint ACL tokens. These values are pseudonyms/compatibility selectors, not authentication credentials. Changing the secret changes fingerprints and endpoint tokens; plan ACL migration before rotation.

## Database ownership and operations

- Supply an explicit file-backed SQLite path. `:memory:` is rejected because each operation opens a separate connection.
- Create the parent directory with restrictive permissions and configure database/WAL/SHM file access according to the host's threat model.
- The library enables SQLite foreign keys and WAL mode. It never chmods, deletes, backs up, or relocates the host's database files.
- Keep one database isolated from unrelated applications. Do not pass the active COS Control Broker database path; this package does not implement cross-version migrations for that service's combined `agents` and A2A schema.
- Back up the database and its WAL consistently. Apply a tested SQLite backup procedure while coordinating writes.
- Run the library behind a host-controlled authentication and authorization layer. Do not expose it directly as a public network endpoint.

## API overview

- `register_endpoint(principal, label, allowed_sender_fingerprints=None)`
- `retire_endpoint(principal, endpoint_id, confirm_label=..., confirm_delete=True)`
- `set_endpoint_acl(principal, endpoint_id, fingerprints_or_sender_tokens)`
- `set_peer_acl(principal, endpoint_id, sender_endpoint_ids)`
- `list_endpoints(principal)` and `directory_list(principal)`
- `send_message(principal, sender_endpoint_id, recipient_endpoint_id, correlation_id, idempotency_key, message, ttl_seconds=None)`
- `read_inbox(principal, endpoint_id, after_seq=0, limit=50)`
- `ack_message(principal, endpoint_id, message_id)`
- `reply_message(principal, sender_endpoint_id, original_message_id, idempotency_key, message, ttl_seconds=None)`

See `tests/test_mailbox.py` for runnable examples and edge cases. Limits are configurable through `MailboxLimits` and retain bounded defaults.

## Scope and non-goals

Not provided: signed identity-header verification; ContextForge/COS Control API client; MCP tool schema or HTTP listener; automatic wake-up/dispatch; background expiry worker; delivery guarantees beyond committed SQLite state; encryption-at-rest; distributed consensus; multi-host SQLite coordination; hard deletion or data-retention policy. The host owns authentication, tenant boundaries, database lifecycle, operational monitoring, notification/dispatch, and any additional authorization checks.

## Validation

The test suite uses temporary SQLite files only. It covers endpoint ownership and ACLs, exact endpoint isolation, idempotent sends, inbox state transitions, expiry, retirement, reply atomicity, database reopen, and input bounds. It does not test the live COS broker, its database, an MCP deployment, cross-host coordination, or a production authentication integration.

## Source lineage

The mailbox implementation was extracted and adapted from `cos_control_broker.py` in [`nplongx/cos-multi-account-mcp`](https://github.com/nplongx/cos-multi-account-mcp). The extraction removes agent registry, ContextForge request/header authentication, MCP server dispatch, and the shared broker database. It retains the mailbox lifecycle and ACL compatibility model while accepting a host-supplied `AuthenticatedPrincipal`.

The upstream `NOTICE` lists IBM ContextForge-derived overlay files separately; `cos_control_broker.py` is not among those listed files. This extraction therefore retains the repository's Apache-2.0 license without copying unrelated IBM attribution notices.

## License

Apache-2.0. See `LICENSE`.

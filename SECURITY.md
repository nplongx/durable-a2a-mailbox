# Security policy

## Trust boundary

This package is a SQLite persistence library. It does not authenticate principals, validate tokens, protect a public HTTP endpoint, or establish that caller-supplied identity assertions are truthful.

- Construct `AuthenticatedPrincipal` only after successful authentication by a trusted host adapter.
- Use separate SQLite files for separate tenant/security domains. The directory API intentionally reveals active endpoint metadata within one database scope.
- Store `identity_secret` in a secret manager. It must be at least 32 bytes. It derives pseudonymous principal fingerprints and endpoint ACL tokens; neither is a login credential.
- Protect the database and its `-wal`/`-shm` files with host-controlled filesystem permissions and backups.
- Do not store secrets or credentials in mailbox message bodies.
- ACL checks and SQLite transactions protect application-level access to this database. They do not defend against a hostile process with direct write/schema access to the database file.
- SQLite WAL is intended for local filesystem use, not multi-host shared-network-filesystem coordination.

## Reporting

Report vulnerabilities privately to the repository owner. Do not include exploit details in public issues.

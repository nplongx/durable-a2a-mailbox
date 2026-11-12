# Contributing

Keep the core library independent of web frameworks, identity providers, MCP runtimes, and application-specific services. Authentication remains a host responsibility. Preserve the explicit SQLite database boundary, transactional message lifecycle, endpoint ownership checks, and idempotency semantics.

Run:

```sh
python -m unittest discover -s tests -v
python -m compileall -q durable_a2a_mailbox tests
```

Changes to principal handling, ACL mode transitions, message lifecycle, reply transaction ordering, or schema migration require dedicated regression tests.

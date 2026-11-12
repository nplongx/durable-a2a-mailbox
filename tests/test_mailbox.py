from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from durable_a2a_mailbox import AuthenticatedPrincipal, MailboxLimits, MailboxStore

SECRET = "test-secret-value-that-is-at-least-32-bytes-long"


class PackageMetadataTests(unittest.TestCase):
    def test_packaged_terminology_matches_repository_glossary(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        repository_glossary = (repository_root / "TERMINOLOGY.md").read_bytes()
        package_glossary = (repository_root / "durable_a2a_mailbox" / "TERMINOLOGY.md").read_bytes()
        self.assertEqual(package_glossary, repository_glossary)


class MailboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "mailbox.sqlite3"
        self.store = MailboxStore(self.db_path, identity_secret=SECRET)
        self.alice = AuthenticatedPrincipal("alice@example.test")
        self.bob = AuthenticatedPrincipal("bob@example.test")
        self.alice_fp = self.store.identity_fingerprint(self.alice)
        self.bob_fp = self.store.identity_fingerprint(self.bob)
        self.alice_ep = self.store.register_endpoint(self.alice, "WS1")["endpoint_id"]
        self.alice_second_ep = self.store.register_endpoint(self.alice, "WS2")["endpoint_id"]
        self.bob_ep = self.store.register_endpoint(self.bob, "WS3")["endpoint_id"]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def send(self, *, key: str = "pilot-001", message: str = "hello") -> dict:
        return self.store.send_message(
            self.alice, self.alice_ep, self.bob_ep,
            "pilot-correlation-001", key, message, 3600,
        )

    def allow_alice(self) -> None:
        self.store.set_endpoint_acl(self.bob, self.bob_ep, [self.alice_fp])

    def test_endpoint_is_private_until_owner_adds_fingerprint_acl(self) -> None:
        with self.assertRaises(PermissionError):
            self.send()
        self.allow_alice()
        result = self.send()
        self.assertEqual(result["state"], "queued")
        self.assertTrue(result["durable"])
        self.assertFalse(result["duplicate"])

    def test_directory_omits_private_principal_identifiers(self) -> None:
        result = self.store.directory_list(self.alice)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["directory_scope"], "authenticated_mailbox_database")
        for endpoint in result["endpoints"]:
            self.assertNotIn("owner_id", endpoint)
            self.assertNotIn("email", endpoint)
        bob_view = self.store.list_endpoints(self.bob)
        self.assertEqual({e["endpoint_id"] for e in bob_view["endpoints"]}, {self.bob_ep})
        self.assertEqual(bob_view["peer_directory_total"], 3)

    def test_plain_mapping_is_not_an_authenticated_principal(self) -> None:
        with self.assertRaises(PermissionError):
            self.store.list_endpoints({"subject": "alice@example.test"})  # type: ignore[arg-type]

    def test_sender_ownership_is_enforced(self) -> None:
        self.allow_alice()
        with self.assertRaises(PermissionError):
            self.store.send_message(
                self.bob, self.alice_ep, self.bob_ep,
                "spoof-correlation", "spoof-1", "spoofed", 3600,
            )

    def test_same_owner_endpoints_are_distinct_under_exact_peer_acl(self) -> None:
        self.store.set_peer_acl(self.bob, self.bob_ep, [self.alice_ep])
        self.assertEqual(self.send()["state"], "queued")
        with self.assertRaises(PermissionError):
            self.store.send_message(
                self.alice, self.alice_second_ep, self.bob_ep,
                "peer-correlation-2", "peer-key-2", "from WS2", 3600,
            )

    def test_legacy_fingerprint_acl_and_endpoint_token_compatibility(self) -> None:
        token = self.store.sender_acl_token(self.alice_ep)
        self.assertRegex(token, r"^[0-9a-f]{64}$")
        self.store.set_endpoint_acl(self.bob, self.bob_ep, [token])
        self.assertEqual(self.send()["state"], "queued")
        with self.assertRaises(PermissionError):
            self.store.send_message(
                self.alice, self.alice_second_ep, self.bob_ep,
                "legacy-correlation-2", "legacy-key-2", "from wrong endpoint", 3600,
            )

    def test_empty_exact_acl_denies_all_and_disables_fingerprint_fallback(self) -> None:
        self.store.set_endpoint_acl(self.bob, self.bob_ep, [self.alice_fp])
        summary = self.store.set_peer_acl(self.bob, self.bob_ep, [])
        self.assertEqual(summary["acl_mode"], "endpoint")
        self.assertEqual(summary["allowed_sender_count"], 0)
        with self.assertRaises(PermissionError):
            self.send()
        with self.assertRaises(ValueError):
            self.store.set_endpoint_acl(self.bob, self.bob_ep, [self.alice_fp])

    def test_peer_acl_requires_owned_recipient_and_active_registered_senders(self) -> None:
        with self.assertRaises(PermissionError):
            self.store.set_peer_acl(self.alice, self.bob_ep, [self.alice_ep])
        with self.assertRaises(ValueError):
            self.store.set_peer_acl(self.bob, self.bob_ep, ["00000000-0000-0000-0000-000000000001"])
        with self.assertRaises(ValueError):
            self.store.set_peer_acl(self.bob, self.bob_ep, [self.alice_ep, self.alice_ep])

    def test_read_receive_ack_and_owner_scope(self) -> None:
        self.allow_alice()
        sent = self.send()
        inbox = self.store.read_inbox(self.bob, self.bob_ep)
        self.assertEqual(len(inbox["events"]), 1)
        self.assertEqual(inbox["events"][0]["message_id"], sent["message_id"])
        self.assertEqual(inbox["events"][0]["state"], "received")
        ack = self.store.ack_message(self.bob, self.bob_ep, sent["message_id"])
        self.assertEqual(ack["state"], "acked")
        self.assertEqual(self.store.read_inbox(self.bob, self.bob_ep)["events"], [])
        with self.assertRaises(PermissionError):
            self.store.read_inbox(self.alice, self.bob_ep)
        with self.assertRaises(PermissionError):
            self.store.ack_message(self.alice, self.alice_ep, sent["message_id"])

    def test_reply_routes_back_preserves_correlation_and_acks_atomically(self) -> None:
        self.store.set_endpoint_acl(self.bob, self.bob_ep, [self.alice_fp])
        self.store.set_endpoint_acl(self.alice, self.alice_ep, [self.bob_fp])
        sent = self.send()
        reply = self.store.reply_message(
            self.bob, self.bob_ep, sent["message_id"], "reply-001", "ack from WS3", 3600
        )
        self.assertTrue(reply["original_message_acked"])
        self.assertEqual(reply["reply_to_message_id"], sent["message_id"])
        self.assertEqual(reply["correlation_id"], "pilot-correlation-001")
        with sqlite3.connect(self.db_path) as db:
            original_state = db.execute(
                "SELECT state FROM a2a_messages WHERE message_id = ?", (sent["message_id"],)
            ).fetchone()[0]
        self.assertEqual(original_state, "acked")
        inbox = self.store.read_inbox(self.alice, self.alice_ep)
        self.assertEqual(inbox["events"][0]["message"], "ack from WS3")
        self.assertEqual(inbox["events"][0]["reply_to_message_id"], sent["message_id"])

    def test_reply_is_idempotent_and_changed_payload_conflicts(self) -> None:
        self.store.set_endpoint_acl(self.bob, self.bob_ep, [self.alice_fp])
        self.store.set_endpoint_acl(self.alice, self.alice_ep, [self.bob_fp])
        sent = self.send()
        first = self.store.reply_message(self.bob, self.bob_ep, sent["message_id"], "reply-key", "answer", 3600)
        replay = self.store.reply_message(self.bob, self.bob_ep, sent["message_id"], "reply-key", "answer", 3600)
        self.assertEqual(first["message_id"], replay["message_id"])
        self.assertTrue(replay["duplicate"])
        with self.assertRaises(ValueError):
            self.store.reply_message(self.bob, self.bob_ep, sent["message_id"], "reply-key", "changed", 3600)

    def test_reply_requires_authenticated_owner_of_sender_endpoint(self) -> None:
        self.allow_alice()
        sent = self.send()
        attacker = AuthenticatedPrincipal("mallory@example.test")
        with self.assertRaises(PermissionError):
            self.store.reply_message(attacker, self.bob_ep, sent["message_id"], "attack", "spoof", 3600)

    def test_reply_requires_reciprocal_acl_and_failure_does_not_ack_original(self) -> None:
        self.allow_alice()
        sent = self.send()
        with self.assertRaises(PermissionError):
            self.store.reply_message(self.bob, self.bob_ep, sent["message_id"], "reply-no-acl", "reply", 3600)
        with sqlite3.connect(self.db_path) as db:
            state = db.execute(
                "SELECT state FROM a2a_messages WHERE message_id = ?", (sent["message_id"],)
            ).fetchone()[0]
        self.assertEqual(state, "queued")

    def test_idempotency_replay_returns_same_message_and_rejects_changed_body(self) -> None:
        self.allow_alice()
        first = self.send()
        replay = self.send()
        self.assertEqual(replay["message_id"], first["message_id"])
        self.assertTrue(replay["duplicate"])
        with self.assertRaises(ValueError):
            self.send(message="different content")
        self.assertEqual(len(self.store.read_inbox(self.bob, self.bob_ep)["events"]), 1)

    def test_acl_revocation_expires_pending_messages(self) -> None:
        self.allow_alice()
        sent = self.send()
        self.store.set_endpoint_acl(self.bob, self.bob_ep, [])
        self.assertEqual(self.store.read_inbox(self.bob, self.bob_ep)["events"], [])
        with sqlite3.connect(self.db_path) as db:
            state = db.execute(
                "SELECT state FROM a2a_messages WHERE message_id = ?", (sent["message_id"],)
            ).fetchone()[0]
        self.assertEqual(state, "expired")

    def test_expired_messages_are_not_delivered(self) -> None:
        self.allow_alice()
        sent = self.send()
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "UPDATE a2a_messages SET expires_at = 0 WHERE message_id = ?", (sent["message_id"],)
            )
        self.assertEqual(self.store.read_inbox(self.bob, self.bob_ep)["events"], [])
        with sqlite3.connect(self.db_path) as db:
            state = db.execute(
                "SELECT state FROM a2a_messages WHERE message_id = ?", (sent["message_id"],)
            ).fetchone()[0]
        self.assertEqual(state, "expired")

    def test_retire_expires_pending_messages_and_preserves_history(self) -> None:
        self.allow_alice()
        outgoing = self.send()
        self.store.set_endpoint_acl(self.alice, self.alice_ep, [self.bob_fp])
        incoming = self.store.send_message(
            self.bob, self.bob_ep, self.alice_ep,
            "incoming-correlation", "incoming-key", "incoming", 3600,
        )
        retired = self.store.retire_endpoint(
            self.alice, self.alice_ep, confirm_label="WS1", confirm_delete=True
        )
        self.assertTrue(retired["retired"])
        self.assertEqual(retired["expired_message_count"], 2)
        self.assertEqual(retired["state"], "disabled")
        with sqlite3.connect(self.db_path) as db:
            states = dict(db.execute(
                "SELECT message_id, state FROM a2a_messages WHERE message_id IN (?, ?)",
                (outgoing["message_id"], incoming["message_id"]),
            ).fetchall())
        self.assertEqual(states, {outgoing["message_id"]: "expired", incoming["message_id"]: "expired"})
        again = self.store.retire_endpoint(
            self.alice, self.alice_ep, confirm_label="WS1", confirm_delete=True
        )
        self.assertTrue(again["already_retired"])
        with self.assertRaises(ValueError):
            self.send(key="after-retire")

    def test_retired_endpoint_does_not_consume_active_endpoint_quota(self) -> None:
        limited = MailboxStore(self.db_path, identity_secret=SECRET,
            limits=MailboxLimits(max_endpoints_per_principal=2))
        with self.assertRaises(ValueError):
            limited.register_endpoint(self.alice, "third-before-retire")
        limited.retire_endpoint(self.alice, self.alice_ep, confirm_label="WS1", confirm_delete=True)
        self.assertEqual(limited.register_endpoint(self.alice, "third-after-retire")["state"], "active")

    def test_pending_inbox_limit_is_enforced(self) -> None:
        limited = MailboxStore(self.db_path, identity_secret=SECRET,
            limits=MailboxLimits(max_pending_per_endpoint=1))
        limited.set_endpoint_acl(self.bob, self.bob_ep, [self.alice_fp])
        limited.send_message(self.alice, self.alice_ep, self.bob_ep, "quota-1", "quota-key-1", "one", 3600)
        with self.assertRaises(ValueError):
            limited.send_message(self.alice, self.alice_ep, self.bob_ep, "quota-2", "quota-key-2", "two", 3600)

    def test_data_persists_when_database_is_reopened(self) -> None:
        self.allow_alice()
        sent = self.send()
        reopened = MailboxStore(self.db_path, identity_secret=SECRET)
        inbox = reopened.read_inbox(self.bob, self.bob_ep)
        self.assertEqual([event["message_id"] for event in inbox["events"]], [sent["message_id"]])

    def test_secret_and_principal_validation(self) -> None:
        with self.assertRaises(ValueError):
            AuthenticatedPrincipal("  ")
        with self.assertRaises(ValueError):
            MailboxStore(Path(self.tmp.name) / "bad.sqlite3", identity_secret="short")
        with self.assertRaises(ValueError):
            MailboxLimits(default_ttl_seconds=601, max_ttl_seconds=600)


    def test_concurrent_idempotent_sends_commit_only_one_message(self) -> None:
        self.allow_alice()

        def send_once() -> dict:
            return self.store.send_message(
                self.alice, self.alice_ep, self.bob_ep,
                "concurrent-correlation", "concurrent-idempotency", "same payload", 3600,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(lambda _index: send_once(), range(2)))
        self.assertEqual(first["message_id"], second["message_id"])
        self.assertEqual(sorted([first["duplicate"], second["duplicate"]]), [False, True])
        with sqlite3.connect(self.db_path) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM a2a_messages WHERE sender_endpoint_id = ? AND idempotency_key = ?",
                (self.alice_ep, "concurrent-idempotency"),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_different_database_path_isolated_from_existing_endpoints(self) -> None:
        other_path = Path(self.tmp.name) / "other-mailbox.sqlite3"
        other = MailboxStore(other_path, identity_secret=SECRET)
        self.assertEqual(other.list_endpoints(self.alice)["endpoints"], [])
        self.assertEqual(other.directory_list(self.alice)["total"], 0)

    def test_retirement_requires_exact_label_and_explicit_confirmation(self) -> None:
        with self.assertRaises(ValueError):
            self.store.retire_endpoint(self.alice, self.alice_ep, confirm_label="WS2", confirm_delete=True)
        with self.assertRaises(ValueError):
            self.store.retire_endpoint(self.alice, self.alice_ep, confirm_label="WS1", confirm_delete=False)
        with self.assertRaises(PermissionError):
            self.store.retire_endpoint(self.bob, self.alice_ep, confirm_label="WS1", confirm_delete=True)

    def test_expired_reply_attempt_persists_expired_state(self) -> None:
        self.allow_alice()
        sent = self.send(key="reply-expired-source")
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE a2a_messages SET expires_at = 0 WHERE message_id = ?", (sent["message_id"],))
        with self.assertRaises(ValueError):
            self.store.reply_message(self.bob, self.bob_ep, sent["message_id"], "reply-to-expired", "late", 3600)
        with sqlite3.connect(self.db_path) as db:
            state = db.execute(
                "SELECT state FROM a2a_messages WHERE message_id = ?", (sent["message_id"],)
            ).fetchone()[0]
        self.assertEqual(state, "expired")


    def test_fresh_database_contains_only_mailbox_tables(self) -> None:
        with sqlite3.connect(self.db_path) as db:
            tables = {
                row[0]
                for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        self.assertIn("a2a_endpoints", tables)
        self.assertIn("a2a_messages", tables)
        self.assertNotIn("agents", tables)

    def test_adds_reply_column_to_legacy_mailbox_schema_without_losing_messages(self) -> None:
        legacy_path = Path(self.tmp.name) / "legacy.sqlite3"
        sender_id = "00000000-0000-4000-8000-000000000001"
        recipient_id = "00000000-0000-4000-8000-000000000002"
        message_id = "00000000-0000-4000-8000-000000000003"
        with sqlite3.connect(legacy_path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("""CREATE TABLE a2a_endpoints (
                endpoint_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, owner_fingerprint TEXT NOT NULL,
                label TEXT NOT NULL, state TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
            )""")
            db.execute("""CREATE TABLE a2a_messages (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL UNIQUE,
                correlation_id TEXT NOT NULL, sender_endpoint_id TEXT NOT NULL REFERENCES a2a_endpoints(endpoint_id),
                sender_owner_id TEXT NOT NULL, sender_fingerprint TEXT NOT NULL,
                recipient_endpoint_id TEXT NOT NULL REFERENCES a2a_endpoints(endpoint_id), body TEXT NOT NULL,
                state TEXT NOT NULL, idempotency_key TEXT NOT NULL, created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL, received_at INTEGER, acked_at INTEGER,
                UNIQUE(sender_endpoint_id, idempotency_key)
            )""")
            db.executemany(
                "INSERT INTO a2a_endpoints VALUES (?, ?, ?, ?, 'active', 1, 1)",
                [
                    (sender_id, "alice", "a" * 64, "sender"),
                    (recipient_id, "bob", "b" * 64, "recipient"),
                ],
            )
            db.execute(
                "INSERT INTO a2a_messages (message_id, correlation_id, sender_endpoint_id, sender_owner_id, sender_fingerprint, recipient_endpoint_id, body, state, idempotency_key, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, 1, 9999999999)",
                (message_id, "legacy-correlation", sender_id, "alice", "a" * 64, recipient_id, "preserve me", "legacy-key"),
            )
        MailboxStore(legacy_path, identity_secret=SECRET)
        with sqlite3.connect(legacy_path) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(a2a_messages)")}
            preserved = db.execute(
                "SELECT body, state, reply_to_message_id FROM a2a_messages WHERE message_id = ?",
                (message_id,),
            ).fetchone()
        self.assertIn("reply_to_message_id", columns)
        self.assertEqual(preserved, ("preserve me", "queued", None))

    def test_memory_database_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MailboxStore(":memory:", identity_secret=SECRET)


if __name__ == "__main__":
    unittest.main()

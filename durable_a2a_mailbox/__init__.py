"""Standalone durable A2A mailbox library."""

from .mailbox import AuthenticatedPrincipal, MailboxLimits, MailboxStore

__all__ = ["AuthenticatedPrincipal", "MailboxLimits", "MailboxStore"]

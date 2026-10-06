"""
Mailer Mailbox provisioning client (L1).

The ONLY place a mailbox SMTP credential is placed in a Mailer request body,
and therefore the only credential-bearing API operations LeadBoost has:

    create_mailbox    POST  /mailboxes              (credential, once)
    activate_mailbox  PATCH /mailboxes/{ref}        (credential, atomic with status=active)

Everything else here (`list_mailboxes`, `disable_mailbox`) carries none.
Ordinary outreach dispatch never touches this module -- see client.py.

Callers (application/services/mailer_mailbox_sync.py) decrypt LeadBoost's
stored credential immediately before one of these calls and `del` it right
after; this module never logs request bodies.

`smtp_use_tls` is always True: Mailer's SMTP sender is STARTTLS-only, so
accounts that require implicit TLS are never sent here (the sync service
refuses them as `unsupported_security_mode`).

Mailbox identity (email_address) is sent only on create. It is never part of
an update, and neither is the organization (Mailer derives that from the key).
"""

from typing import Optional
from urllib.parse import quote

from core.infrastructure.mailing_agent.client import MailerResponse, send_request

MAILBOXES_PATH = "/mailboxes"


def _item_path(reference: str) -> str:
    # `reference` came from a Mailer response, but is still quoted so it can
    # never change the path structure.
    return f"{MAILBOXES_PATH}/{quote(reference, safe='')}"


async def create_mailbox(
    organization_id: int,
    *,
    email_address: str,
    smtp_host: str,
    smtp_port: int,
    smtp_username: str,
    smtp_password: str,
) -> MailerResponse:
    body = {
        "email_address": email_address,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_use_tls": True,
        "smtp_username": smtp_username,
        "smtp_password": smtp_password,
    }
    return await send_request("POST", MAILBOXES_PATH, organization_id=organization_id, json_body=body)


async def list_mailboxes(organization_id: int) -> MailerResponse:
    return await send_request("GET", MAILBOXES_PATH, organization_id=organization_id)


async def disable_mailbox(organization_id: int, reference: str) -> MailerResponse:
    return await send_request(
        "PATCH", _item_path(reference), organization_id=organization_id, json_body={"status": "disabled"}
    )


async def activate_mailbox(
    organization_id: int,
    reference: str,
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_username: str,
    smtp_password: str,
) -> MailerResponse:
    """One atomic PATCH: transport + credential + status=active. There is no
    window where the new credential exists on an ACTIVE mailbox without the
    matching verified transport settings."""
    body = {
        "status": "active",
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_use_tls": True,
        "smtp_username": smtp_username,
        "smtp_password": smtp_password,
    }
    return await send_request("PATCH", _item_path(reference), organization_id=organization_id, json_body=body)

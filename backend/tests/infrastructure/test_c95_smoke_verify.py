"""scripts/c95_smoke_verify.py: assertion logic on JSON fixtures, transport guards, and 'no secrets / no bodies printed'."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "c95_smoke_verify", BACKEND / "scripts" / "c95_smoke_verify.py"
)
sv = importlib.util.module_from_spec(_spec)
sys.modules["c95_smoke_verify"] = sv
_spec.loader.exec_module(sv)

SUBJ, BODY = "Re: Quick question", "Thanks for the reply.\nHere is the exact text."
DIG = sv.content_digest(SUBJ, BODY)
SUBJ2, BODY2 = "Re: Another", "A second, different draft."
DIG2 = sv.content_digest(SUBJ2, BODY2)
INBOUND = {
    "direction": "inbound",
    "subject": "hi",
    "body": "hello",
    "body_truncated": False,
    "delivery_state": None,
    "message_reference": "a" * 32,
}


def sent_bundle() -> dict:
    return {
        "action": {
            "id": 7,
            "state": "submitted",
            "idempotency_key": "reply:" + "b" * 40,
            "reply_to_message_reference": "a" * 32,
            "approved_by_user_id": 3,
            "approved_at": "2026-11-25T10:00:00Z",
            "approved_draft_version": 1,
            "approved_content_sha256": DIG,
            "dispatch_attempts": 1,
            "mailing_agent_reference": "ref-sent",
        },
        "draft": {"state": "sent", "subject": SUBJ, "body": BODY, "content_sha256": DIG, "draft_version": 1},
        "mailer_state": {
            "availability": "available",
            "mailer": {
                "delivery_state": "sent",
                "has_more": False,
                "messages": [
                    INBOUND,
                    {
                        "direction": "outbound",
                        "subject": SUBJ,
                        "body": BODY,
                        "body_truncated": False,
                        "delivery_state": "sent",
                    },
                    {**INBOUND, "message_reference": "c" * 32},
                ],
            },
        },
        "mailer_conversation": {
            "action": {"state": "sent"},
            "messages": [
                {"direction": "outbound", "mailing_agent_reference": "ref-sent", "delivery_state": "sent"}
            ],
        },
        "mailer_draft": {"state": "sent", "content_sha256": DIG},
    }


def cancelled_bundle() -> dict:
    return {
        "action": {
            "id": 8,
            "state": "cancelled",
            "approved_content_sha256": None,
            "submitted_at": None,
            "dispatch_attempts": 0,
        },
        "draft": {
            "state": "cancelled",
            "subject": SUBJ2,
            "body": BODY2,
            "content_sha256": DIG2,
            "draft_version": 1,
        },
        "mailer_state": {
            "availability": "available",
            "mailer": {
                "delivery_state": "failed",
                "has_more": False,
                "messages": [
                    {
                        "direction": "outbound",
                        "subject": SUBJ,
                        "body": BODY,
                        "body_truncated": False,
                        "delivery_state": "sent",
                    },
                    {
                        "direction": "outbound",
                        "subject": SUBJ2,
                        "body": BODY2,
                        "body_truncated": False,
                        "delivery_state": "failed",
                    },
                ],
            },
        },
        "mailer_draft": {"state": "cancelled"},
    }


def run(sent=None, cancelled=None, inbound_min=2):
    r = sv.Report()
    if sent is not None:
        sv.evaluate_sent(r, sent, inbound_min)
    if cancelled is not None:
        sv.evaluate_cancelled(r, cancelled, sent)
    return r


def failed(r) -> list[str]:
    return [c.name for c in r.checks if not c.ok and c.blocking]


def test_digest_matches_the_mailer_contract_vectors():
    # mailer_agent/mail/content_digest.py: sha256 of {"body","subject"} sorted, compact, ensure_ascii False; None subject -> ""
    assert sv.content_digest(None, "x") == sv.content_digest("", "x")
    assert sv.content_digest("é", "ü") != sv.content_digest(
        "e\u0301", "u\u0308"
    ), "no normalisation (NFC != NFD)"
    assert sv.content_digest("a\n", "b") != sv.content_digest("a\r\n", "b")
    import hashlib

    assert sv.content_digest("S", "B") == hashlib.sha256(b'{"body":"B","subject":"S"}').hexdigest()


def test_a_healthy_sent_and_cancelled_pair_passes():
    r = run(sent_bundle(), cancelled_bundle())
    assert r.ok, failed(r)


def test_sent_twice_is_detected():
    b = sent_bundle()
    b["mailer_state"]["mailer"]["messages"].append(copy.deepcopy(b["mailer_state"]["mailer"]["messages"][1]))
    assert any("exactly ONE" in n for n in failed(run(b)))


def test_mailer_view_with_two_dispatches_for_one_action_is_detected():
    b = sent_bundle()
    b["mailer_conversation"]["messages"].append(
        {"direction": "outbound", "mailing_agent_reference": "ref-sent", "delivery_state": "sent"}
    )
    assert any("exactly one outbound message carries" in n for n in failed(run(b)))


def test_not_sent_or_wrong_state_is_detected():
    for state in ("awaiting_approval", "queued", "unknown", "failed"):
        b = sent_bundle()
        b["draft"]["state"] = state
        assert any("draft read: state is sent" in n for n in failed(run(b))), state
    b = sent_bundle()
    b["mailer_state"]["mailer"]["delivery_state"] = "unknown"
    assert any("delivery_state sent" in n for n in failed(run(b)))


def test_digest_mismatches_are_detected():
    b = sent_bundle()
    b["draft"]["content_sha256"] = "0" * 64
    assert any("equals the digest approved" in n for n in failed(run(b)))
    b = sent_bundle()
    b["draft"]["body"] = BODY + " (edited)"
    assert any("recomputed here" in n for n in failed(run(b)))
    b = sent_bundle()
    b["mailer_state"]["mailer"]["messages"][1]["body"] = (
        BODY + "!"
    )  # what was stored/sent differs from what was approved
    assert any("exactly ONE" in n for n in failed(run(b)))


def test_missing_approval_evidence_is_detected():
    b = sent_bundle()
    b["action"]["approved_by_user_id"] = None
    assert any("approver" in n for n in failed(run(b)))
    b = sent_bundle()
    b["action"]["approved_draft_version"] = 2
    assert any("version is 1" in n for n in failed(run(b)))


def test_inbound_reply_count_is_checked_only_when_asked():
    b = sent_bundle()
    b["mailer_state"]["mailer"]["messages"] = b["mailer_state"]["mailer"]["messages"][:2]
    assert not any("inbound" in n for n in failed(run(b, inbound_min=None)))
    assert any("inbound" in n for n in failed(run(b, inbound_min=2)))


def test_cancelled_draft_that_was_sent_anyway_is_detected():
    c = cancelled_bundle()
    c["mailer_state"]["mailer"]["messages"][1]["delivery_state"] = "sent"
    names = failed(run(sent_bundle(), c))
    assert any("NO `sent` outbound message" in n for n in names)


def test_cancelled_action_with_approval_or_wrong_state_is_detected():
    c = cancelled_bundle()
    c["action"]["approved_content_sha256"] = DIG2
    assert any("no approval was ever recorded" in n for n in failed(run(None, c)))
    c = cancelled_bundle()
    c["action"]["state"] = "submitted"
    assert any("state is cancelled" in n for n in failed(run(None, c)))
    c = cancelled_bundle()
    c["draft"]["state"] = "queued"
    assert any("draft read: state is cancelled" in n for n in failed(run(None, c)))


def test_same_content_for_both_drafts_is_flagged():
    c = cancelled_bundle()
    c["draft"].update(subject=SUBJ, body=BODY, content_sha256=DIG)
    assert any("differs from the sent one" in n for n in failed(run(sent_bundle(), c)))


def test_dispatch_attempts_is_informational_only():
    b = sent_bundle()
    b["action"]["dispatch_attempts"] = 2
    r = run(b)
    assert r.ok and any(c.name.startswith("sent action: one dispatch attempt") and not c.ok for c in r.checks)


@pytest.mark.parametrize(
    "url", ["http://lead.example.com", "ftp://x", "lead.example.com", "http://127.0.0.1.evil.example"]
)
def test_non_https_origins_are_refused(url):
    with pytest.raises(sv.UsageError):
        sv.check_origin(url, "X")


def test_https_and_loopback_origins_are_accepted():
    assert sv.check_origin("https://api.example.com/", "X") == "https://api.example.com"
    assert sv.check_origin("http://127.0.0.1:8000", "X") == "http://127.0.0.1:8000"


def test_client_requires_credentials_and_pairs_mailer_vars():
    with pytest.raises(sv.UsageError):
        sv.Client({})
    with pytest.raises(sv.UsageError):
        sv.Client({"LB_BASE_URL": "https://a.example.com"})
    with pytest.raises(sv.UsageError):
        sv.Client(
            {
                "LB_BASE_URL": "https://a.example.com",
                "LB_ACCESS_TOKEN": "t",
                "MAILER_BASE_URL": "https://m.example.com",
            }
        )


def test_cli_fixture_mode_prints_no_bodies_no_subjects_and_exit_codes(tmp_path, capsys):
    f = tmp_path / "b.json"
    f.write_text(json.dumps({"sent": sent_bundle(), "cancelled": cancelled_bundle()}))
    assert sv.main(["--fixture", str(f)], env={}) == 0
    out = capsys.readouterr().out
    assert BODY not in out and SUBJ not in out and BODY2 not in out and SUBJ2 not in out
    assert DIG not in out, "only a 12-character digest prefix may be printed"
    bad = sent_bundle()
    bad["draft"]["state"] = "queued"
    f.write_text(json.dumps({"sent": bad}))
    assert sv.main(["--fixture", str(f)], env={}) == 1
    assert sv.main([], env={}) == 2


def test_the_module_only_ever_issues_get_requests_and_the_login_post():
    src = (BACKEND / "scripts" / "c95_smoke_verify.py").read_text(encoding="utf-8")
    assert src.count("data=") == 2 or 'method="POST" if data is not None' in src
    assert "/api/v2/login" in src and "approve" not in src.split("def http_json")[1].split("def render")[
        0
    ].replace("approved", "")
    for forbidden in ("/release", "/cancel", "/dispatch", 'reply-draft", method', ".post("):
        assert forbidden not in src.split("class Client")[1].split("def render")[0], forbidden

import { AxiosError, type AxiosResponse } from "axios";
import { describe, expect, it } from "vitest";
import {
  deriveReplyView,
  findReplyAction,
  hasCompleteDraft,
  isLegacyReplyAction,
  isValidMessageReference,
  selectOrdinaryActiveAction,
  type ReplyInFlight,
} from "@/components/outreach/reply-state";
import { REPLY_POLL_MAX_ATTEMPTS, replyPollInterval, shouldRetryReplyRead } from "@/features/outreach/hooks";
import type { OutreachAction, OutreachStateValue, ReplyDraft, ReplyDraftState } from "@/types/api";

const REF = "0123456789abcdef0123456789abcdef";
const DIGEST = "a".repeat(64);
const IDLE: ReplyInFlight = { creating: false, starting: false, approving: false, sending: false, cancelling: false };

function action(over: Partial<OutreachAction> = {}): OutreachAction {
  return {
    id: 7, organization_id: 1, lead_id: 3, email_account_id: 2, mode: "manual", state: "pending_review",
    recipient_email: "jane@example.com", recipient_name: "Jane", subject: null, body: "", correlation_id: null,
    idempotency_key: "reply:abc", reason: null, approved_by_user_id: null, approved_at: null, submitted_at: null,
    cancelled_at: null, mailing_agent_reference: null, dispatch_attempts: 0, last_dispatch_error: null,
    reply_to_message_reference: REF, reply_draft_requested_at: "2026-10-31T10:00:00Z",
    approved_content_sha256: null, approved_draft_version: null, created_at: "2026-10-31T10:00:00Z", updated_at: null,
    ...over,
  };
}

function draft(state: ReplyDraftState, over: Partial<ReplyDraft> = {}): ReplyDraft {
  const hasContent = !["not_started", "generating", "failed", "cancelled"].includes(state);
  return {
    state,
    subject: hasContent ? "Re: Hello" : null,
    body: hasContent ? "Hi Jane,\n\nThe exact reply." : null,
    content_sha256: hasContent ? DIGEST : null,
    draft_version: hasContent ? 1 : null,
    updated_at: "2026-10-31T10:01:00Z",
    ...over,
  };
}

const view = (a: OutreachAction | null, d: ReplyDraft | undefined, flight: Partial<ReplyInFlight> = {}, err = false) =>
  deriveReplyView({ action: a, draft: d, draftError: err, inFlight: { ...IDLE, ...flight } });

describe("isValidMessageReference", () => {
  it("accepts exactly 32 lowercase hex characters", () => {
    expect(isValidMessageReference(REF)).toBe(true);
  });
  it.each([
    ["null", null], ["undefined", undefined], ["empty", ""], ["number", 12], ["object", {}],
    ["31 chars", REF.slice(1)], ["33 chars", REF + "a"], ["uppercase", REF.toUpperCase()], ["non-hex", "g".repeat(32)],
    ["an RFC Message-ID", "<abc@example.com>"], ["with spaces", REF.slice(0, 31) + " "], ["with newline", REF + "\n"],
  ])("rejects %s", (_name, value) => {
    expect(isValidMessageReference(value)).toBe(false);
  });
});

describe("approve is unavailable until a complete draft is ready", () => {
  it("is not offered before anything exists", () => {
    const v = view(null, undefined);
    expect(v.stage).toBe("idle");
    expect(v.canApprove).toBe(false);
    expect(v.canSend).toBe(false);
    expect(v.canGenerate).toBe(true);
  });

  it("is not offered while the draft is generating, or before it was requested", () => {
    expect(view(action(), draft("generating")).canApprove).toBe(false);
    expect(view(action(), undefined).stage).toBe("generating");
    expect(view(action({ reply_draft_requested_at: null }), undefined).stage).toBe("created");
    expect(view(action({ reply_draft_requested_at: null }), undefined).canApprove).toBe(false);
  });

  it("is offered only for a ready, complete, fingerprinted draft on an action awaiting approval", () => {
    const v = view(action(), draft("awaiting_approval"));
    expect(v.stage).toBe("review");
    expect(v.label).toBe("Draft ready for review");
    expect(v.showDraft).toBe(true);
    expect(v.canApprove).toBe(true);
  });

  it.each([
    ["no digest", { content_sha256: null }],
    ["empty digest", { content_sha256: "" }],
    ["no version", { draft_version: null }],
    ["no subject", { subject: null }],
    ["no body", { body: null }],
  ])("is refused when the draft is incomplete (%s)", (_n, over) => {
    const d = draft("awaiting_approval", over as Partial<ReplyDraft>);
    expect(hasCompleteDraft(d)).toBe(false);
    const v = view(action(), d);
    expect(v.canApprove).toBe(false);
    expect(v.showDraft).toBe(false);
    expect(v.stage).toBe("unavailable");
  });

  it("is refused while any request is in flight", () => {
    for (const k of ["creating", "starting", "approving", "sending", "cancelling"] as const) {
      const v = view(action(), draft("awaiting_approval"), { [k]: true });
      expect(v.busy).toBe(true);
      expect(v.canApprove).toBe(false);
      expect(v.canCancel).toBe(false);
    }
  });

  it("is refused for a failed generation and a failed draft read", () => {
    expect(view(action(), draft("failed")).stage).toBe("generation_failed");
    expect(view(action(), draft("failed")).canApprove).toBe(false);
    const unreadable = view(action(), undefined, {}, true);
    expect(unreadable.stage).toBe("unavailable");
    expect(unreadable.canApprove).toBe(false);
  });
});

describe("send is unavailable until approval has succeeded", () => {
  it("is not offered while the draft is only awaiting review", () => {
    expect(view(action(), draft("awaiting_approval")).canSend).toBe(false);
  });

  it("is offered once approved, with the approved digest stored", () => {
    const a = action({ state: "approved", approved_content_sha256: DIGEST, approved_draft_version: 1 });
    const v = view(a, draft("awaiting_approval"));
    expect(v.stage).toBe("approved");
    expect(v.label).toBe("Approved");
    expect(v.canSend).toBe(true);
    expect(v.canApprove).toBe(false);
  });

  it.each([["null", null], ["empty", ""]])("is refused when the approved digest is %s", (_n, sha) => {
    const a = action({ state: "approved", approved_content_sha256: sha });
    expect(view(a, draft("awaiting_approval")).canSend).toBe(false);
  });

  it("is refused while a send or any other request is in flight (duplicate-click protection)", () => {
    const a = action({ state: "approved", approved_content_sha256: DIGEST });
    expect(view(a, draft("awaiting_approval"), { sending: true }).canSend).toBe(false);
    expect(view(a, draft("awaiting_approval"), { cancelling: true }).canSend).toBe(false);
  });

  it("allows a retry after a release that did not complete, with the same approval", () => {
    const a = action({ state: "dispatch_failed", approved_content_sha256: DIGEST, dispatch_attempts: 1 });
    const v = view(a, draft("awaiting_approval"));
    expect(v.stage).toBe("release_failed");
    expect(v.canSend).toBe(true);
  });

  it.each(["queued", "sending", "sent", "failed", "unknown", "cancelled"] as ReplyDraftState[])(
    "is never offered once the Mailing Agent reports %s",
    (state) => {
      const a = action({ state: "submitted", approved_content_sha256: DIGEST, submitted_at: "2026-10-31T10:05:00Z" });
      expect(view(a, draft(state)).canSend).toBe(false);
    }
  );
});

describe("delivery states stay honest", () => {
  const submitted = action({ state: "submitted", approved_content_sha256: DIGEST });
  it.each([
    ["queued", "queued", "Ready to send"],
    ["sending", "sending", "Sending"],
    ["sent", "sent", "Sent (SMTP accepted)"],
    ["failed", "failed", "Failed"],
    ["unknown", "unknown", "Delivery unconfirmed"],
  ] as [ReplyDraftState, string, string][])("%s -> %s", (state, stage, label) => {
    const v = view(submitted, draft(state));
    expect(v.stage).toBe(stage);
    expect(v.label).toBe(label);
  });

  it("never treats approval or a queue acceptance as 'sent'", () => {
    expect(view(action({ state: "approved", approved_content_sha256: DIGEST }), draft("awaiting_approval")).stage).not.toBe("sent");
    expect(view(submitted, draft("queued")).stage).not.toBe("sent");
    expect(view(submitted, undefined).stage).not.toBe("sent");
  });

  it("an unknown outcome offers neither send nor cancel (it must not be resent)", () => {
    const v = view(submitted, draft("unknown"));
    expect(v.canSend).toBe(false);
    expect(v.canCancel).toBe(false);
  });

  it("trusts the Mailing Agent over a stale local record after an ambiguous release", () => {
    // LeadBoost recorded dispatch_failed, but the Mailing Agent says the message went out.
    const a = action({ state: "dispatch_failed", approved_content_sha256: DIGEST, dispatch_attempts: 1 });
    expect(view(a, draft("sent")).stage).toBe("sent");
    expect(view(a, draft("sent")).canSend).toBe(false);
  });

  it("shows cancelled from either source and offers nothing", () => {
    expect(view(action({ state: "cancelled" }), undefined).stage).toBe("cancelled");
    expect(view(action(), draft("cancelled")).stage).toBe("cancelled");
    const v = view(action({ state: "cancelled" }), draft("cancelled"));
    expect([v.canApprove, v.canSend, v.canCancel, v.canGenerate]).toEqual([false, false, false, false]);
  });
});

describe("cancellation", () => {
  it.each<[OutreachStateValue, ReplyDraftState]>([
    ["pending_review", "generating"],
    ["pending_review", "awaiting_approval"],
    ["pending_review", "failed"],
  ])("is offered before approval (%s / %s)", (state, d) => {
    expect(view(action({ state }), draft(d)).canCancel).toBe(true);
  });
  it("is offered after approval and after a failed release", () => {
    expect(view(action({ state: "approved", approved_content_sha256: DIGEST }), draft("awaiting_approval")).canCancel).toBe(true);
    expect(view(action({ state: "dispatch_failed", approved_content_sha256: DIGEST }), draft("awaiting_approval")).canCancel).toBe(true);
  });
  it("is withheld once the message is released", () => {
    const a = action({ state: "submitted", approved_content_sha256: DIGEST });
    for (const s of ["queued", "sending", "sent"] as ReplyDraftState[]) expect(view(a, draft(s)).canCancel).toBe(false);
  });
  it("is withheld when a 'failed' release actually succeeded (the ambiguous case)", () => {
    // LeadBoost recorded dispatch_failed, so by its own state Cancel looks allowed -- but the Mailing Agent says the
    // message is already released. Offering Cancel there would promise something that is no longer true.
    const a = action({ state: "dispatch_failed", approved_content_sha256: DIGEST, dispatch_attempts: 1 });
    for (const s of ["queued", "sending", "sent", "unknown"] as ReplyDraftState[]) {
      const v = view(a, draft(s));
      expect(v.canCancel, `cancel must not be offered when the Mailing Agent reports ${s}`).toBe(false);
      expect(v.canSend).toBe(false);
    }
    // ...whereas a release that genuinely did not land can still be cancelled.
    expect(view(a, draft("awaiting_approval")).canCancel).toBe(true);
  });
});

describe("selecting actions", () => {
  const ordinary = action({ id: 1, reply_to_message_reference: null });
  const reply = action({ id: 2 });
  const cancelledOrdinary = action({ id: 3, state: "cancelled", reply_to_message_reference: null });

  it("the ordinary action is never a reply, even when the reply is newer", () => {
    expect(selectOrdinaryActiveAction([reply, ordinary])?.id).toBe(1);
    expect(selectOrdinaryActiveAction([reply])).toBeUndefined();
    expect(selectOrdinaryActiveAction([cancelledOrdinary, ordinary])?.id).toBe(1);
    expect(selectOrdinaryActiveAction(undefined)).toBeUndefined();
  });
  it("finds the reply for exactly one inbound message", () => {
    expect(findReplyAction([ordinary, reply], REF)?.id).toBe(2);
    expect(findReplyAction([ordinary, reply], "f".repeat(32))).toBeNull();
    expect(findReplyAction(undefined, REF)).toBeNull();
  });
});

describe("polling policy is bounded", () => {
  it("polls only while the draft is still moving", () => {
    for (const s of ["generating", "queued", "sending"] as ReplyDraftState[]) expect(replyPollInterval(s, 0)).toBeGreaterThan(0);
    for (const s of ["awaiting_approval", "sent", "failed", "unknown", "cancelled", "not_started"] as ReplyDraftState[]) {
      expect(replyPollInterval(s, 0)).toBe(false);
    }
    expect(replyPollInterval(undefined, 0)).toBe(false);
  });
  it("backs off, caps the interval, and is non-decreasing", () => {
    let prev = 0;
    for (let n = 0; n < REPLY_POLL_MAX_ATTEMPTS; n++) {
      const i = replyPollInterval("generating", n) as number;
      expect(i).toBeGreaterThanOrEqual(prev);
      expect(i).toBeLessThanOrEqual(15_000);
      prev = i;
    }
    expect(replyPollInterval("generating", 0)).toBe(2_000);
    expect(replyPollInterval("generating", 1)).toBe(3_000);
    expect(replyPollInterval("generating", 29)).toBe(15_000);
  });
  it("gives up after a bounded number of reads -- it can never poll forever", () => {
    expect(replyPollInterval("generating", REPLY_POLL_MAX_ATTEMPTS - 1)).not.toBe(false);
    expect(replyPollInterval("generating", REPLY_POLL_MAX_ATTEMPTS)).toBe(false);
    expect(replyPollInterval("generating", 10_000)).toBe(false);
  });
});

describe("read retry is bounded and transient-only", () => {
  const httpError = (status: number) =>
    new AxiosError("x", String(status), undefined, undefined, { status, data: {}, statusText: "", headers: {}, config: {} } as unknown as AxiosResponse);
  it("retries network failures and 5xx at most twice", () => {
    const network = new AxiosError("Network Error", "ERR_NETWORK");
    expect(shouldRetryReplyRead(0, network)).toBe(true);
    expect(shouldRetryReplyRead(1, httpError(502))).toBe(true);
    expect(shouldRetryReplyRead(2, httpError(502))).toBe(false);
  });
  it("never retries a 4xx -- that is an answer, not a glitch", () => {
    for (const s of [400, 401, 404, 409, 422]) expect(shouldRetryReplyRead(0, httpError(s))).toBe(false);
  });
});

describe("replies created before exact-content review existed (C9.4)", () => {
  const legacy = (state: OutreachStateValue, over: Partial<OutreachAction> = {}) =>
    action({ state, reply_draft_requested_at: null, approved_content_sha256: null, approved_draft_version: null, ...over });

  it.each<OutreachStateValue>(["approved", "dispatching", "dispatch_failed", "submitted"])(
    "%s with no requested draft is legacy",
    (state) => {
      expect(isLegacyReplyAction(legacy(state))).toBe(true);
      expect(view(legacy(state), undefined).stage).toBe("legacy");
    }
  );

  it("is not legacy: a pending reply (it approved nothing), a cancelled one, one with a draft requested, or a non-reply", () => {
    expect(isLegacyReplyAction(legacy("pending_review"))).toBe(false);
    expect(view(legacy("pending_review"), undefined).stage).toBe("created");   // takes the NEW flow
    expect(isLegacyReplyAction(legacy("cancelled"))).toBe(false);
    expect(isLegacyReplyAction(action({ state: "approved", approved_content_sha256: DIGEST }))).toBe(false);
    expect(isLegacyReplyAction(action({ state: "approved", reply_to_message_reference: null, reply_draft_requested_at: null }))).toBe(false);
    expect(isLegacyReplyAction(null)).toBe(false);
  });

  it.each<OutreachStateValue>(["approved", "dispatching", "dispatch_failed", "submitted"])(
    "an old approval is never offered as approval of a new draft (%s)",
    (state) => {
      const v = view(legacy(state), undefined);
      expect(v.label).toBe("Earlier reply (not reviewed)");
      expect([v.canApprove, v.canSend, v.canGenerate, v.showDraft]).toEqual([false, false, false, false]);
    }
  );

  it("never reads as 'ready to send' or 'sent', even if a draft object were somehow supplied", () => {
    for (const s of ["queued", "sent", "awaiting_approval", "unknown"] as ReplyDraftState[]) {
      const v = view(legacy("submitted"), draft(s));
      expect(v.stage).toBe("legacy");
      expect([v.canApprove, v.canSend, v.showDraft]).toEqual([false, false, false]);
    }
  });

  it("can be cancelled only where LeadBoost itself still allows it (approved, dispatch_failed)", () => {
    expect(view(legacy("approved"), undefined).canCancel).toBe(true);
    expect(view(legacy("dispatch_failed"), undefined).canCancel).toBe(true);
    expect(view(legacy("dispatching"), undefined).canCancel).toBe(false);
    expect(view(legacy("submitted"), undefined).canCancel).toBe(false);
  });
});

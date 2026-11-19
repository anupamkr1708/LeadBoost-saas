import type { OutreachAction, ReplyDraft } from "@/types/api";

// C9.5: what the reply composer should show and allow, derived from two facts the server owns -- the LeadBoost
// reply action and the Mailing Agent's draft -- plus whether a request is currently in flight. Pure: no React, no
// network, no clock. Every rule that decides whether "Approve" or "Send" is available lives here so it can be
// read, and tested, in one place.
//
// The one rule worth stating up front: Send is available only after approval has SUCCEEDED and the approved
// digest is stored on the action; Approve is available only while a complete, fingerprinted draft is on screen.

/** An inbound message can be replied to only when it carries a well-formed opaque reference (32 lowercase hex).
 * Anything else -- absent, null, a different shape -- gets no Reply action. */
export function isValidMessageReference(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{32}$/.test(value);
}

/** The lead's ordinary outreach action: its first non-cancelled action that is NOT a reply. Replies are separate
 * actions managed in the reply composer; if one were ever taken for the ordinary action, the ordinary Approve /
 * Send controls would act on it. */
export function selectOrdinaryActiveAction(actions: OutreachAction[] | undefined): OutreachAction | undefined {
  return (actions ?? []).find((a) => a.state !== "cancelled" && !a.reply_to_message_reference);
}

/** The (single) reply action created for one specific inbound message, matched by its opaque reference. */
export function findReplyAction(actions: OutreachAction[] | undefined, reference: string): OutreachAction | null {
  return (actions ?? []).find((a) => a.reply_to_message_reference === reference) ?? null;
}

export type ReplyStage =
  | "idle" //               nothing created yet
  | "creating" //           creating the reply action / asking for the draft
  | "created" //            action exists but the draft was never requested (e.g. the request failed)
  | "generating" //         the Mailing Agent is writing the draft
  | "unavailable" //        the draft could not be read right now
  | "review" //             complete draft on screen, awaiting the user's approval
  | "generation_failed" //  no draft will ever exist for this reply
  | "approved" //           approved; ready to send (nothing sent yet)
  | "release_failed" //     send was attempted but not completed; safe to retry
  | "queued" //             released, waiting for the Mailing Agent to send
  | "sending"
  | "sent" //               SMTP accepted the message (not proof of inbox delivery)
  | "failed"
  | "unknown" //            outcome unconfirmed: must not be resent
  | "legacy" //             created before exact-content review existed; cannot be reviewed or sent
  | "cancelled";

export const REPLY_STAGE_LABEL: Record<ReplyStage, string> = {
  idle: "Not started",
  creating: "Creating reply",
  created: "Reply created",
  generating: "Generating reply",
  unavailable: "Draft unavailable",
  review: "Draft ready for review",
  generation_failed: "Generation failed",
  approved: "Approved",
  release_failed: "Not sent yet",
  queued: "Ready to send",
  sending: "Sending",
  sent: "Sent (SMTP accepted)",
  failed: "Failed",
  unknown: "Delivery unconfirmed",
  legacy: "Earlier reply (not reviewed)",
  cancelled: "Cancelled",
};

export interface ReplyInFlight {
  creating: boolean;
  starting: boolean;
  approving: boolean;
  sending: boolean;
  cancelling: boolean;
}

export interface ReplyViewInput {
  action: OutreachAction | null;
  draft: ReplyDraft | undefined;
  /** The draft read has failed (after its bounded retries). */
  draftError: boolean;
  inFlight: ReplyInFlight;
}

export interface ReplyView {
  stage: ReplyStage;
  label: string;
  /** True when the draft's subject and body are present and may be displayed. */
  showDraft: boolean;
  canGenerate: boolean;
  canApprove: boolean;
  canSend: boolean;
  canCancel: boolean;
  /** Something is in flight: every mutating control is disabled (duplicate-click protection). */
  busy: boolean;
}

const anyInFlight = (f: ReplyInFlight) => f.creating || f.starting || f.approving || f.sending || f.cancelling;

/** A draft is displayable/approvable only when ALL of its content fields arrived together. */
export function hasCompleteDraft(draft: ReplyDraft | undefined): draft is ReplyDraft & {
  subject: string;
  body: string;
  content_sha256: string;
  draft_version: number;
} {
  return (
    !!draft &&
    typeof draft.subject === "string" &&
    typeof draft.body === "string" &&
    typeof draft.content_sha256 === "string" &&
    draft.content_sha256.length > 0 &&
    typeof draft.draft_version === "number"
  );
}

/** A reply action created BEFORE exact-content approval existed (C9.4). It was approved as an action and its text was
 * written afterwards, so it has no requested draft and no approved digest. Such an approval is never reused for a new
 * draft: the reply can't be reviewed, restarted or sent -- only cancelled while that is still allowed. (A C9.4 reply
 * still awaiting review approved nothing and simply takes the new flow, so it is not legacy.) */
export function isLegacyReplyAction(action: OutreachAction | null | undefined): boolean {
  return (
    !!action &&
    !!action.reply_to_message_reference &&
    !action.reply_draft_requested_at &&
    (action.state === "approved" ||
      action.state === "dispatching" ||
      action.state === "dispatch_failed" ||
      action.state === "submitted")
  );
}

function stageAfterRelease(draft: ReplyDraft | undefined): ReplyStage {
  switch (draft?.state) {
    case "sent":
      return "sent";
    case "sending":
      return "sending";
    case "failed":
      return "failed";
    case "unknown":
      return "unknown";
    case "cancelled":
      return "cancelled";
    default:
      return "queued";
  }
}

export function deriveReplyStage({ action, draft, draftError, inFlight }: ReplyViewInput): ReplyStage {
  if (action?.state === "cancelled" || draft?.state === "cancelled") return "cancelled";

  if (!action) return inFlight.creating || inFlight.starting ? "creating" : "idle";
  if (isLegacyReplyAction(action)) return "legacy";

  switch (action.state) {
    case "pending_review": {
      if (!action.reply_draft_requested_at) return inFlight.starting ? "creating" : "created";
      if (draftError && !draft) return "unavailable";
      if (!draft) return "generating";
      if (draft.state === "generating") return "generating";
      if (draft.state === "awaiting_approval") return hasCompleteDraft(draft) ? "review" : "unavailable";
      if (draft.state === "failed") return "generation_failed";
      return stageAfterRelease(draft); // released by another session: show the truth
    }
    case "approved":
      if (draft && draft.state !== "awaiting_approval" && draft.state !== "generating") return stageAfterRelease(draft);
      return "approved";
    case "dispatching":
      return "sending";
    case "dispatch_failed":
      // A release may have succeeded even though the response was lost; trust the Mailing Agent's own state.
      if (draft && draft.state !== "awaiting_approval" && draft.state !== "generating") return stageAfterRelease(draft);
      return "release_failed";
    case "submitted":
      return stageAfterRelease(draft);
    default:
      return "unavailable";
  }
}

export function deriveReplyView(input: ReplyViewInput): ReplyView {
  const { action, draft, inFlight } = input;
  const stage = deriveReplyStage(input);
  const busy = anyInFlight(inFlight);
  const contentStages: ReplyStage[] = ["review", "approved", "release_failed", "queued", "sending", "sent", "failed", "unknown"];

  const canGenerate = !busy && (stage === "idle" || stage === "created");

  // Approve: only while a COMPLETE, fingerprinted draft is on screen for an action still awaiting approval.
  const canApprove =
    !busy &&
    stage === "review" &&
    action?.state === "pending_review" &&
    draft?.state === "awaiting_approval" &&
    hasCompleteDraft(draft);

  // Send: only after approval SUCCEEDED -- the approved digest is stored on the action -- and nothing is
  // already in flight, sending, sent, unknown or cancelled.
  const canSend =
    !busy &&
    (stage === "approved" || stage === "release_failed") &&
    (action?.state === "approved" || action?.state === "dispatch_failed") &&
    typeof action.approved_content_sha256 === "string" &&
    action.approved_content_sha256.length > 0;

  const canCancel =
    !busy &&
    !!action &&
    (action.state === "pending_review" || action.state === "approved" || action.state === "dispatch_failed") &&
    stage !== "cancelled" &&
    stage !== "queued" && // released: the Mailing Agent owns it now
    stage !== "sending" &&
    stage !== "sent" &&
    stage !== "unknown";
  // (A legacy reply that already left LeadBoost -- dispatching or submitted -- is excluded by the action-state test
  // above; a legacy approved or dispatch_failed one remains cancellable, which is the only exit from those states.)

  return {
    stage,
    label: REPLY_STAGE_LABEL[stage],
    showDraft: contentStages.includes(stage) && hasCompleteDraft(draft),
    canGenerate,
    canApprove,
    canSend,
    canCancel,
    busy,
  };
}

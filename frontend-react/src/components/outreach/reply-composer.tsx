"use client";

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Clock, HelpCircle, Loader2, RotateCcw, Send, ShieldCheck, X, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { deriveReplyView, type ReplyStage } from "@/components/outreach/reply-state";
import {
  useApproveReplyDraft,
  useCancelReplyAction,
  useCreateReplyAction,
  useRefreshMailerState,
  useReplyDraft,
  useSendApprovedReply,
  useStartReplyDraft,
} from "@/features/outreach/hooks";
import { normalizeApiError } from "@/lib/api-client";
import { formatDateTime } from "@/lib/utils";
import type { Lead, OutreachAction } from "@/types/api";

// C9.5: review, approve and send ONE generated reply to ONE selected inbound message.
//
//   Generate draft  ->  read the exact subject/body  ->  Approve exact draft  ->  Send approved reply
//
// Approval and sending are two separate, explicit actions: approving records a decision and sends nothing;
// "Send approved reply" is the only control that releases the message. The subject and body shown are the
// Mailing Agent's PERSISTED draft (read through the LeadBoost API) -- never the lead's outreach text and never
// anything this component composed -- and the digest sent with Approve is the one belonging to the text on screen.
//
// SECURITY: the inbound message and the generated draft are third-party-influenced text. They are rendered ONLY
// as React text nodes (CSS preserves the line breaks) -- never as HTML. Do not add a raw-HTML or markup renderer
// here; a backend test guards this directory for exactly that. The opaque message reference is data: it is passed
// along to create the reply and is never displayed.

const STAGE_STYLE: Record<ReplyStage, { className: string; Icon: typeof Clock }> = {
  idle: { className: "text-muted-foreground", Icon: Clock },
  creating: { className: "text-sky-300", Icon: Loader2 },
  created: { className: "text-muted-foreground", Icon: Clock },
  generating: { className: "text-sky-300", Icon: Loader2 },
  unavailable: { className: "text-amber-300", Icon: AlertTriangle },
  review: { className: "text-amber-300", Icon: ShieldCheck },
  generation_failed: { className: "text-rose-300", Icon: XCircle },
  approved: { className: "text-sky-300", Icon: CheckCircle2 },
  release_failed: { className: "text-rose-300", Icon: AlertTriangle },
  queued: { className: "text-sky-300", Icon: Clock },
  sending: { className: "text-sky-300", Icon: Loader2 },
  sent: { className: "text-emerald-400", Icon: CheckCircle2 },
  failed: { className: "text-rose-300", Icon: XCircle },
  unknown: { className: "text-amber-300", Icon: HelpCircle },
  legacy: { className: "text-muted-foreground", Icon: HelpCircle },
  cancelled: { className: "text-muted-foreground", Icon: XCircle },
};

const SPINNING: ReadonlySet<ReplyStage> = new Set<ReplyStage>(["creating", "generating", "sending"]);
const REFRESH_CONVERSATION_ON: ReadonlySet<ReplyStage> = new Set<ReplyStage>(["sent", "unknown", "failed"]);

export interface ReplyComposerProps {
  lead: Lead;
  /** The verified sender mailbox this reply will go out from. */
  senderAccountId: number | null;
  senderLabel: string | null;
  /** The exact inbound message being answered. Data only -- never rendered. */
  messageReference: string;
  inboundSubject: string | null;
  inboundBody: string;
  /** True when the Mailer shortened the inbound message it returned (`body_truncated`). The excerpt on screen is then
   *  NOT the whole message, and the user is told so. Says nothing about the generated draft, which is always complete. */
  inboundBodyTruncated: boolean;
  inboundReceivedAt: string | null;
  /** The action whose conversation is on screen; it is re-read after a send. */
  conversationActionId: number;
  /** This inbound message's reply action, if one already exists (the server allows exactly one). */
  replyAction: OutreachAction | null;
  onClose: () => void;
}

export function ReplyComposer({
  lead,
  senderAccountId,
  senderLabel,
  messageReference,
  inboundSubject,
  inboundBody,
  inboundBodyTruncated,
  inboundReceivedAt,
  conversationActionId,
  replyAction,
  onClose,
}: ReplyComposerProps) {
  const createReply = useCreateReplyAction(lead.id);
  const startDraft = useStartReplyDraft(lead.id);
  const approveDraft = useApproveReplyDraft(lead.id);
  const sendReply = useSendApprovedReply(lead.id, conversationActionId);
  const cancelReply = useCancelReplyAction(lead.id);
  const refreshConversation = useRefreshMailerState(conversationActionId);

  // The action created in this session, until the lead's action list (the source of truth) catches up.
  const [created, setCreated] = useState<OutreachAction | null>(null);
  const action = replyAction ?? created;

  const draftQuery = useReplyDraft(action?.id ?? 0, !!action?.reply_draft_requested_at);
  const draft = draftQuery.data;

  const view = deriveReplyView({
    action,
    draft,
    draftError: draftQuery.isError,
    inFlight: {
      creating: createReply.isPending,
      starting: startDraft.isPending,
      approving: approveDraft.isPending,
      sending: sendReply.isPending,
      cancelling: cancelReply.isPending,
    },
  });

  // A synchronous lock on top of the disabled buttons: two clicks in the same tick can both fire before React
  // re-renders the disabled state, so the handler itself refuses to run twice.
  const locked = useRef(false);
  const once = async (fn: () => Promise<unknown>) => {
    if (locked.current) return;
    locked.current = true;
    try {
      await fn();
    } catch {
      // The mutations report their own (safe) errors.
    } finally {
      locked.current = false;
    }
  };

  const generate = () =>
    once(async () => {
      const target = action ?? (await createReply.mutateAsync(toCreatePayload()));
      if (!action) setCreated(target);
      await startDraft.mutateAsync(target.id);
    });

  const toCreatePayload = () => {
    if (senderAccountId === null) throw new Error("no sender");
    return {
      lead_id: lead.id,
      email_account_id: senderAccountId,
      reply_to_message_reference: messageReference,
      mode: "manual" as const,
    };
  };

  const approve = () =>
    once(async () => {
      // The digest of the text that is ON SCREEN right now, and nothing else.
      if (!action || !draft?.content_sha256) return;
      await approveDraft.mutateAsync({ actionId: action.id, contentSha256: draft.content_sha256 });
    });

  const send = () =>
    once(async () => {
      if (action) await sendReply.mutateAsync(action.id);
    });

  const cancel = () =>
    once(async () => {
      if (action) await cancelReply.mutateAsync(action.id);
    });

  // Once the outcome of a send is known, re-read the real conversation (once per outcome).
  const refreshedFor = useRef<ReplyStage | null>(null);
  useEffect(() => {
    if (REFRESH_CONVERSATION_ON.has(view.stage) && refreshedFor.current !== view.stage) {
      refreshedFor.current = view.stage;
      refreshConversation();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refreshConversation is stable per action id
  }, [view.stage]);

  const { className: stageClass, Icon } = STAGE_STYLE[view.stage];
  // A draft too large to show in full can never be reviewed or approved, so "try again" would be false comfort.
  const tooLarge = draftQuery.isError && normalizeApiError(draftQuery.error).errorCode === "reply_draft_too_large";
  const pollExhausted = view.stage === "generating" && draftQuery.pollsExhausted && !draftQuery.isFetching;
  const recipient = action?.recipient_name || lead.contact_name || lead.company_name || "the recipient";
  const recipientEmail = action?.recipient_email || lead.email;

  return (
    <section className="mt-4 rounded-xl border border-white/10 bg-white/[0.03] p-4" aria-label="Reply">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Reply</p>
        <button type="button" onClick={onClose} className="text-muted-foreground hover:text-foreground" aria-label="Close reply">
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* The exact message being answered. */}
      <div className="mt-2 rounded-lg border border-sky-500/20 bg-sky-500/5 px-3 py-2">
        <p className="text-xs text-muted-foreground">
          Replying to{inboundReceivedAt ? ` · ${formatDateTime(inboundReceivedAt)}` : ""}
        </p>
        {inboundSubject && <p className="mt-1 break-words text-xs font-medium">{inboundSubject}</p>}
        <p className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs text-muted-foreground">{inboundBody}</p>
        {inboundBodyTruncated && (
          <p role="note" className="mt-1 text-xs italic text-amber-300">
            Shortened preview: this is only part of the original message. Read the whole message in your mailbox
            before approving a reply that depends on what it says. The reply draft below is complete.
          </p>
        )}
      </div>

      <dl className="mt-3 grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[auto,1fr]">
        <dt className="text-muted-foreground">To</dt>
        <dd className="break-words">
          {recipient}
          {recipientEmail ? ` <${recipientEmail}>` : ""}
        </dd>
        <dt className="text-muted-foreground">From</dt>
        <dd className="break-words">{senderLabel ?? "No verified sender mailbox"}</dd>
      </dl>

      <p role="status" aria-live="polite" className={`mt-3 flex items-center gap-1.5 text-xs font-medium ${stageClass}`}>
        <Icon className={`h-3.5 w-3.5 ${SPINNING.has(view.stage) ? "animate-spin" : ""}`} /> {view.label}
        {draft?.updated_at && view.showDraft && (
          <span className="font-normal text-muted-foreground">· {formatDateTime(draft.updated_at)}</span>
        )}
      </p>

      {view.stage === "generating" && !pollExhausted && (
        <p className="mt-1 text-xs text-muted-foreground">
          Writing the reply. Nothing is sent while it is being written, and you will review it before anything goes out.
        </p>
      )}
      {pollExhausted && (
        <div className="mt-1.5 space-y-1.5">
          <p className="text-xs text-muted-foreground">This is taking longer than expected.</p>
          <Button variant="secondary" size="sm" loading={draftQuery.isFetching} onClick={() => draftQuery.refetch()}>
            <RotateCcw className="h-3.5 w-3.5" /> Check again
          </Button>
        </div>
      )}
      {view.stage === "unavailable" && tooLarge && (
        <p className="mt-1.5 text-xs text-rose-300/90">
          The generated reply is too long to show in full, so it can&apos;t be reviewed or approved, and nothing was sent.
          Cancel this reply to close it.
        </p>
      )}
      {view.stage === "unavailable" && !tooLarge && (
        <div className="mt-1.5 space-y-1.5">
          <p className="text-xs text-muted-foreground">We couldn&apos;t load the draft right now. Nothing has been sent.</p>
          <Button variant="secondary" size="sm" loading={draftQuery.isFetching} onClick={() => draftQuery.refetch()}>
            <RotateCcw className="h-3.5 w-3.5" /> Try again
          </Button>
        </div>
      )}
      {view.stage === "legacy" && (
        <p className="mt-1.5 text-xs text-muted-foreground">
          This reply was created before exact-content review existed. Its earlier approval can&apos;t be applied to a new
          message, so it can&apos;t be reviewed or sent from here. Anything that was sent appears in the conversation above.
          {view.canCancel ? " You can cancel it to close it; replying to this message again isn\u2019t possible afterwards." : ""}
        </p>
      )}
      {view.stage === "generation_failed" && (
        <p className="mt-1 text-xs text-rose-300/90">
          A reply could not be written for this message, and nothing was sent. Cancel this reply to close it.
        </p>
      )}

      {/* The exact persisted subject and body -- complete, plain text. */}
      {view.showDraft && draft && (
        <div className="mt-3 rounded-lg border border-white/10 bg-black/20 px-3 py-2">
          <p className="text-xs text-muted-foreground">
            {view.stage === "review"
              ? "This is the exact message that will be sent if you approve it."
              : "This is the message that was approved."}
          </p>
          <p className="mt-2 break-words text-xs font-medium">{draft.subject}</p>
          <p className="mt-1.5 whitespace-pre-wrap break-words text-xs text-foreground/90">{draft.body}</p>
        </div>
      )}

      {view.stage === "approved" && (
        <p className="mt-2 text-xs text-muted-foreground">Approved. Nothing has been sent yet. Send it when you are ready.</p>
      )}
      {view.stage === "release_failed" && (
        <p className="mt-2 text-xs text-rose-300/90">
          It wasn&apos;t released for sending this time. You can try again; the same approved message will be used.
        </p>
      )}
      {view.stage === "queued" && (
        <p className="mt-2 text-xs text-muted-foreground">Released and waiting to be sent by the Mailing Agent.</p>
      )}
      {view.stage === "sent" && (
        <p className="mt-2 text-xs text-muted-foreground">
          The mail server accepted this message. That doesn&apos;t confirm it reached the recipient&apos;s inbox.
        </p>
      )}
      {view.stage === "failed" && (
        <p className="mt-2 text-xs text-rose-300/90">The message could not be sent. It was not delivered.</p>
      )}
      {view.stage === "unknown" && (
        <p className="mt-2 text-xs text-amber-300/90">
          We can&apos;t confirm whether this was delivered. Please don&apos;t send it again — the message may already have
          gone out.
        </p>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        {(view.stage === "idle" || view.stage === "created") && (
          <Button
            size="sm"
            disabled={!view.canGenerate || senderAccountId === null}
            loading={createReply.isPending || startDraft.isPending}
            onClick={generate}
          >
            <Send className="h-3.5 w-3.5" /> Generate reply draft
          </Button>
        )}
        {view.stage === "review" && (
          <Button size="sm" disabled={!view.canApprove} loading={approveDraft.isPending} onClick={approve}>
            <ShieldCheck className="h-3.5 w-3.5" /> Approve exact draft
          </Button>
        )}
        {(view.stage === "approved" || view.stage === "release_failed") && (
          <Button size="sm" disabled={!view.canSend} loading={sendReply.isPending} onClick={send}>
            <Send className="h-3.5 w-3.5" /> {view.stage === "release_failed" ? "Try sending again" : "Send approved reply"}
          </Button>
        )}
        {view.canCancel && (
          <Button variant="secondary" size="sm" loading={cancelReply.isPending} onClick={cancel}>
            <XCircle className="h-3.5 w-3.5" /> Cancel reply
          </Button>
        )}
        <Button variant="secondary" size="sm" onClick={onClose}>
          Close
        </Button>
      </div>

      {senderAccountId === null && (view.stage === "idle" || view.stage === "created") && (
        <p className="mt-2 text-xs text-muted-foreground">Add and verify a sender mailbox to reply from.</p>
      )}
    </section>
  );
}

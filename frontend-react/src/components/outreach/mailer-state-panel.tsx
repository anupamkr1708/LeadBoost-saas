"use client";

import { AlertTriangle, CheckCircle2, Clock, HelpCircle, Loader2, RotateCcw, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useOutreachMailerState } from "@/features/outreach/hooks";
import { formatDateTime } from "@/lib/utils";
import type { MailerConversationMessage, MailerDeliveryState, OutreachStateValue } from "@/types/api";

// C9.3: a READ-ONLY view of what the Mailing Agent reports for one outreach action: its
// authoritative delivery state and the actual conversation with the recipient. It shows
// next to -- and never replaces or "corrects" -- the action's own LeadBoost state, and it
// has no controls that change anything (the only button re-reads).
//
// SECURITY: every message subject/body is third-party-controlled text (inbound mail is
// untrusted). It is rendered ONLY as React text nodes -- never as HTML -- so it can't
// become markup or script. Do not introduce dangerouslySetInnerHTML or a markdown/HTML
// renderer here; a backend test guards this file for exactly that.

const DELIVERY: Record<MailerDeliveryState, { label: string; className: string; Icon: typeof Clock }> = {
  queued: { label: "Queued", className: "text-sky-300", Icon: Clock },
  sending: { label: "Sending…", className: "text-sky-300", Icon: Loader2 },
  sent: { label: "Sent", className: "text-emerald-400", Icon: CheckCircle2 },
  failed: { label: "Failed", className: "text-rose-300", Icon: XCircle },
  // "unknown" is NOT "failed": the send may or may not have happened.
  unknown: { label: "Delivery unconfirmed", className: "text-amber-300", Icon: HelpCircle },
};

function DeliveryLine({ state, updatedAt }: { state: MailerDeliveryState; updatedAt: string | null }) {
  const { label, className, Icon } = DELIVERY[state];
  return (
    <div className="space-y-1">
      <p className={`flex items-center gap-1.5 text-xs font-medium ${className}`}>
        <Icon className={`h-3.5 w-3.5 ${state === "sending" ? "animate-spin" : ""}`} /> {label}
        {updatedAt && <span className="font-normal text-muted-foreground">· {formatDateTime(updatedAt)}</span>}
      </p>
      {state === "unknown" && (
        <p className="text-xs text-amber-300/90">
          We can&apos;t confirm whether this was delivered. Please don&apos;t resend it — the message may already have
          gone out.
        </p>
      )}
    </div>
  );
}

function Message({ message }: { message: MailerConversationMessage }) {
  const outbound = message.direction === "outbound";
  return (
    <li className={`rounded-lg border px-3 py-2 ${outbound ? "border-white/10 bg-white/5" : "border-sky-500/20 bg-sky-500/5"}`}>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">{outbound ? "You" : "Recipient"}</span>
        <span>
          {message.created_at ? formatDateTime(message.created_at) : null}
          {outbound && message.delivery_state ? ` · ${DELIVERY[message.delivery_state].label}` : null}
        </span>
      </div>
      {message.subject && <p className="mt-1 break-words text-xs font-medium">{message.subject}</p>}
      {/* plain text only: a React text node, with line breaks preserved by CSS */}
      <p className="mt-1 whitespace-pre-wrap break-words text-xs text-muted-foreground">{message.body}</p>
      {message.body_truncated && <p className="mt-1 text-xs italic text-muted-foreground">Message shortened.</p>}
    </li>
  );
}

export function MailerStatePanel({
  actionId,
  leadboostState,
}: {
  actionId: number;
  leadboostState: OutreachStateValue;
}) {
  const { data, isLoading, isError, refetch, isFetching } = useOutreachMailerState(actionId);

  const retry = (
    <Button variant="secondary" size="sm" loading={isFetching} onClick={() => refetch()}>
      <RotateCcw className="h-3.5 w-3.5" /> Refresh
    </Button>
  );

  let content: React.ReactNode;
  if (isLoading) {
    content = (
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Checking delivery status…
      </p>
    );
  } else if (isError || !data) {
    content = (
      <div className="space-y-1.5">
        <p className="text-xs text-muted-foreground">Couldn&apos;t load delivery details right now.</p>
        {retry}
      </div>
    );
  } else if (data.availability === "not_dispatched") {
    content = <p className="text-xs text-muted-foreground">Not handed off yet — nothing has been sent.</p>;
  } else if (data.availability === "not_found_at_mailer") {
    content = (
      <div className="space-y-1.5">
        <p className="text-xs text-muted-foreground">
          The Mailing Agent has no record of this outreach yet, so nothing has been sent from it.
        </p>
        {retry}
      </div>
    );
  } else if (data.availability === "mailer_unavailable" || !data.mailer) {
    content = (
      <div className="space-y-1.5">
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <AlertTriangle className="h-3.5 w-3.5" /> Delivery status is temporarily unavailable.
        </p>
        {retry}
      </div>
    );
  } else {
    const m = data.mailer;
    // LeadBoost's record and the Mailing Agent disagree: say so, plainly. Nothing is rewritten.
    const mismatch = leadboostState === "dispatch_failed" && m.delivery_state !== "failed";
    content = (
      <div className="space-y-3">
        <DeliveryLine state={m.delivery_state} updatedAt={m.updated_at} />
        {mismatch && (
          <p className="flex items-start gap-1.5 rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-2 text-xs text-amber-200">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
              Our last hand-off attempt was recorded as failed, but the Mailing Agent reports this outreach as{" "}
              {DELIVERY[m.delivery_state].label.toLowerCase()}. Retrying could send it twice.
            </span>
          </p>
        )}
        {m.messages.length > 0 ? (
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">
              Conversation with this recipient{m.has_more ? " — showing the most recent messages" : ""}
            </p>
            <ul className="space-y-2">
              {m.messages.map((message, i) => (
                <Message key={`${message.direction}-${message.created_at ?? "x"}-${i}`} message={message} />
              ))}
            </ul>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">No messages yet.</p>
        )}
        {retry}
      </div>
    );
  }

  return (
    <div className="mt-4 border-t border-white/10 pt-4">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Delivery &amp; conversation</p>
      <div className="mt-2">{content}</div>
    </div>
  );
}

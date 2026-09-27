"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Copy, Mail, ExternalLink, CheckCircle2, Send, ShieldAlert, XCircle, RotateCcw, Loader2 } from "lucide-react";
import toast from "react-hot-toast";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { QualificationBadge } from "@/components/shared/status-badge";
import { useUpdateLead } from "@/features/leads/hooks";
import { useEmailAccounts } from "@/features/email-accounts/hooks";
import {
  useOutreachActionsForLead,
  useCreateOutreachAction,
  useApproveOutreachAction,
  useCancelOutreachAction,
  useDispatchOutreachAction,
} from "@/features/outreach/hooks";
import { useAuthStore } from "@/store/auth-store";
import { ensureProtocol, formatDateTime, getHostname } from "@/lib/utils";
import type { Lead, OutreachStateValue } from "@/types/api";

// P1.4: display treatment for the closed OutreachAction state vocabulary
// -- see backend core/domain/models/outreach_action.py::OutreachState.
// "dispatching" is a short-lived, machine-only claim state a client
// would only ever observe via a concurrent GET while another request's
// dispatch is in flight -- never as the result of its own dispatch call,
// which always resolves before returning. Deliberately no "delivered"/
// "replied" entry: this table (and this card) only ever knows as far as
// SUBMITTED -- see that model's docstring for why delivery/reply
// tracking is a separate, later concern owned by the Mailing Agent.
const OUTREACH_STATE_STYLES: Record<OutreachStateValue, { label: string; className: string }> = {
  pending_review: { label: "Awaiting approval", className: "bg-amber-500/15 text-amber-300 border-amber-500/30" },
  approved: { label: "Approved", className: "bg-sky-500/15 text-sky-300 border-sky-500/30" },
  dispatching: { label: "Sending…", className: "bg-sky-500/15 text-sky-300 border-sky-500/30" },
  submitted: {
    label: "Submitted to Mailing Agent",
    className: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  },
  dispatch_failed: { label: "Dispatch failed", className: "bg-rose-500/15 text-rose-300 border-rose-500/30" },
  cancelled: { label: "Cancelled", className: "bg-white/10 text-muted-foreground border-white/10" },
};

export function OutreachCard({ lead }: { lead: Lead }) {
  const [draft, setDraft] = useState(lead.outreach_message ?? "");
  const updateLead = useUpdateLead(lead.id);
  const dirty = draft !== (lead.outreach_message ?? "");

  const orgId = useAuthStore((s) => s.user?.organization_id ?? 0);
  const { data: emailAccounts } = useEmailAccounts(orgId);
  const verifiedSenders = useMemo(
    () => (emailAccounts ?? []).filter((a) => a.is_active && a.verification_status === "verified"),
    [emailAccounts]
  );
  const [senderId, setSenderId] = useState<string>("");
  const selectedSenderId = senderId || (verifiedSenders[0] ? String(verifiedSenders[0].id) : "");

  // The lead's most recent non-cancelled outreach action, if any -- a
  // cancelled action doesn't block preparing a new one. Only one action
  // is ever "live" for a lead in this phase (no campaigns/sequences --
  // see the P1.4 brief's explicit non-goals), so the first match is enough.
  const { data: actions } = useOutreachActionsForLead(lead.id);
  const activeAction = useMemo(() => (actions ?? []).find((a) => a.state !== "cancelled"), [actions]);

  const createAction = useCreateOutreachAction(lead.id);
  const approveAction = useApproveOutreachAction(lead.id);
  const cancelAction = useCancelOutreachAction(lead.id);
  const dispatchAction = useDispatchOutreachAction(lead.id);

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-display text-sm font-semibold">{lead.company_name || getHostname(lead.website)}</p>
          <a
            href={ensureProtocol(lead.website)}
            target="_blank"
            rel="noreferrer"
            className="mt-0.5 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-primary-300"
          >
            {getHostname(lead.website)} <ExternalLink className="h-3 w-3" />
          </a>
        </div>
        <div className="flex items-center gap-2">
          <QualificationBadge label={lead.qualification_label} />
          {activeAction && (
            <span
              className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium ${OUTREACH_STATE_STYLES[activeAction.state].className}`}
            >
              {activeAction.state === "dispatching" && <Loader2 className="h-3 w-3 animate-spin" />}
              {OUTREACH_STATE_STYLES[activeAction.state].label}
            </span>
          )}
        </div>
      </div>

      <Textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={5} className="mt-4" />
      {activeAction && (activeAction.state === "pending_review" || activeAction.state === "approved") && (
        <p className="mt-1.5 text-xs text-muted-foreground">
          This draft is for your own reference — the prepared outreach action already has its own fixed copy of the
          message and won&apos;t pick up further edits here.
        </p>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <Button
          variant="secondary"
          size="sm"
          onClick={() => {
            navigator.clipboard.writeText(draft);
            toast.success("Draft copied to clipboard.");
          }}
        >
          <Copy className="h-3.5 w-3.5" /> Copy
        </Button>
        {dirty && (
          <Button size="sm" loading={updateLead.isPending} onClick={() => updateLead.mutate({ outreach_message: draft })}>
            Save edits
          </Button>
        )}
        {lead.email && (
          <Button variant="secondary" size="sm" asChild>
            <a
              href={`mailto:${lead.email}?subject=${encodeURIComponent(
                `Reaching out to ${lead.company_name ?? getHostname(lead.website)}`
              )}&body=${encodeURIComponent(draft)}`}
            >
              <Mail className="h-3.5 w-3.5" /> Open in email
            </a>
          </Button>
        )}
      </div>

      <div className="mt-5 border-t border-white/10 pt-4">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Outreach action</p>

        {!activeAction && (
          <div className="mt-2 space-y-2">
            {verifiedSenders.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No verified sender mailbox yet.{" "}
                <Link href="/organization" className="text-primary-300 hover:underline">
                  Add and verify one
                </Link>{" "}
                to prepare outreach through the Mailing Agent.
              </p>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                {verifiedSenders.length > 1 && (
                  <Select value={selectedSenderId} onValueChange={setSenderId}>
                    <SelectTrigger className="h-9 w-56">
                      <SelectValue placeholder="Choose sender" />
                    </SelectTrigger>
                    <SelectContent>
                      {verifiedSenders.map((a) => (
                        <SelectItem key={a.id} value={String(a.id)}>
                          {a.display_name || a.email_address}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
                <Button
                  size="sm"
                  disabled={!lead.outreach_message || !selectedSenderId || dirty}
                  loading={createAction.isPending}
                  onClick={() =>
                    createAction.mutate({ lead_id: lead.id, email_account_id: Number(selectedSenderId) })
                  }
                >
                  <Send className="h-3.5 w-3.5" /> Prepare outreach
                </Button>
              </div>
            )}
            {!lead.outreach_message && (
              <p className="text-xs text-muted-foreground">This lead has no generated message yet.</p>
            )}
            {dirty && lead.outreach_message && (
              <p className="text-xs text-muted-foreground">
                Save your edits above before preparing outreach — Prepare outreach always uses the saved message, not
                the unsaved draft, so it&apos;s disabled while there are unsaved changes.
              </p>
            )}
          </div>
        )}

        {activeAction?.state === "pending_review" && (
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="sm" loading={approveAction.isPending} onClick={() => approveAction.mutate(activeAction.id)}>
              Approve
            </Button>
            <Button
              variant="secondary"
              size="sm"
              loading={cancelAction.isPending}
              onClick={() => cancelAction.mutate(activeAction.id)}
            >
              <XCircle className="h-3.5 w-3.5" /> Cancel
            </Button>
          </div>
        )}

        {activeAction?.state === "approved" && (
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="sm" loading={dispatchAction.isPending} onClick={() => dispatchAction.mutate(activeAction.id)}>
              <Send className="h-3.5 w-3.5" /> Send via Mailing Agent
            </Button>
            <Button
              variant="secondary"
              size="sm"
              loading={cancelAction.isPending}
              onClick={() => cancelAction.mutate(activeAction.id)}
            >
              <XCircle className="h-3.5 w-3.5" /> Cancel
            </Button>
          </div>
        )}

        {activeAction?.state === "dispatching" && (
          <p className="mt-2 flex items-center gap-1.5 text-xs text-sky-300">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Handing off to the Mailing Agent — this only takes a
            moment.
          </p>
        )}

        {activeAction?.state === "dispatch_failed" && (
          <div className="mt-2 space-y-1.5">
            <p className="flex items-center gap-1.5 text-xs text-rose-300">
              <ShieldAlert className="h-3.5 w-3.5" /> Could not hand this off to the Mailing Agent yet.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" loading={dispatchAction.isPending} onClick={() => dispatchAction.mutate(activeAction.id)}>
                <RotateCcw className="h-3.5 w-3.5" /> Retry
              </Button>
              <Button
                variant="secondary"
                size="sm"
                loading={cancelAction.isPending}
                onClick={() => cancelAction.mutate(activeAction.id)}
              >
                <XCircle className="h-3.5 w-3.5" /> Cancel
              </Button>
            </div>
          </div>
        )}

        {activeAction?.state === "submitted" && (
          <p className="mt-2 flex items-center gap-1.5 text-xs text-emerald-400">
            <CheckCircle2 className="h-3.5 w-3.5" /> Submitted {formatDateTime(activeAction.submitted_at)}
          </p>
        )}
      </div>
    </Card>
  );
}

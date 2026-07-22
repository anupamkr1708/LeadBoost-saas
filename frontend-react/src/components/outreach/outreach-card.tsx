"use client";

import { useState } from "react";
import { Copy, Mail, ExternalLink, CheckCircle2 } from "lucide-react";
import toast from "react-hot-toast";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { QualificationBadge } from "@/components/shared/status-badge";
import { useUpdateLead } from "@/features/leads/hooks";
import { ensureProtocol, formatDateTime, getHostname } from "@/lib/utils";
import type { Lead } from "@/types/api";

export function OutreachCard({ lead }: { lead: Lead }) {
  const [draft, setDraft] = useState(lead.outreach_message ?? "");
  const updateLead = useUpdateLead(lead.id);
  const dirty = draft !== (lead.outreach_message ?? "");

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
          {lead.outreach_sent && (
            <span className="flex items-center gap-1 text-xs text-emerald-400">
              <CheckCircle2 className="h-3.5 w-3.5" /> Sent {formatDateTime(lead.outreach_sent_at)}
            </span>
          )}
        </div>
      </div>

      <Textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={5} className="mt-4" />

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
            <a href={`mailto:${lead.email}?subject=${encodeURIComponent(`Reaching out to ${lead.company_name ?? getHostname(lead.website)}`)}&body=${encodeURIComponent(draft)}`}>
              <Mail className="h-3.5 w-3.5" /> Open in email
            </a>
          </Button>
        )}
      </div>
    </Card>
  );
}

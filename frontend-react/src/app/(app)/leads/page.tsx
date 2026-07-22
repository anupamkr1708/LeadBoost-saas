"use client";

import { useState } from "react";
import { Users } from "lucide-react";
import { LeadsTable } from "@/components/leads/leads-table";
import { LeadDetailDrawer } from "@/components/leads/lead-detail-drawer";
import { AddLeadDialog } from "@/components/leads/add-lead-dialog";
import { useLeads } from "@/features/leads/hooks";

export default function LeadsPage() {
  const { data: leads, isLoading } = useLeads({ limit: 200 });
  const [activeLeadId, setActiveLeadId] = useState<number | null>(null);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary-500/15 text-primary-300">
            <Users className="h-5 w-5" />
          </div>
          <div>
            <h1 className="font-display text-2xl font-semibold tracking-tight">Leads</h1>
            <p className="text-sm text-muted-foreground">{leads?.length ?? 0} total leads</p>
          </div>
        </div>
        <AddLeadDialog />
      </div>

      <LeadsTable leads={leads ?? []} loading={isLoading} onOpenLead={setActiveLeadId} />
      <LeadDetailDrawer leadId={activeLeadId} onOpenChange={(open) => !open && setActiveLeadId(null)} />
    </div>
  );
}

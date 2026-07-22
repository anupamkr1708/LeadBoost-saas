"use client";

import { Sheet, SheetContent } from "@/components/ui/sheet";
import { LeadDetailContent } from "@/components/leads/lead-detail-content";

interface LeadDetailDrawerProps {
  leadId: number | null;
  onOpenChange: (open: boolean) => void;
}

/** Slide-over drawer used from the leads table so users never lose table context. */
export function LeadDetailDrawer({ leadId, onOpenChange }: LeadDetailDrawerProps) {
  return (
    <Sheet open={leadId !== null} onOpenChange={onOpenChange}>
      <SheetContent className="overflow-y-auto pb-8">
        {leadId !== null && <LeadDetailContent leadId={leadId} onDeleted={() => onOpenChange(false)} />}
      </SheetContent>
    </Sheet>
  );
}

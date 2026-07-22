"use client";

import { useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { LeadDetailContent } from "@/components/leads/lead-detail-content";

export default function LeadDetailPage({ params }: { params: { id: string } }) {
  const router = useRouter();
  const leadId = Number(params.id);

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" onClick={() => router.push("/leads")}>
        <ArrowLeft className="h-3.5 w-3.5" /> Back to leads
      </Button>
      <Card className="p-6">
        <LeadDetailContent leadId={leadId} onDeleted={() => router.push("/leads")} />
      </Card>
    </div>
  );
}

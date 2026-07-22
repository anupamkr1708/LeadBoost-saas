"use client";

import { Mail } from "lucide-react";
import { OutreachCard } from "@/components/outreach/outreach-card";
import { EmptyState } from "@/components/shared/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useLeads } from "@/features/leads/hooks";

export default function OutreachPage() {
  const { data: leads, isLoading } = useLeads({ limit: 200 });

  const withDrafts = (leads ?? []).filter((l) => !!l.outreach_message);
  const sent = withDrafts.filter((l) => l.outreach_sent);
  const pending = withDrafts.filter((l) => !l.outreach_sent);

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary-500/15 text-primary-300">
          <Mail className="h-5 w-5" />
        </div>
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight">Outreach</h1>
          <p className="text-sm text-muted-foreground">AI-drafted messages, ready to review and send.</p>
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-40 w-full" />
          ))}
        </div>
      ) : withDrafts.length === 0 ? (
        <EmptyState
          icon={Mail}
          title="No outreach drafts yet"
          description="Once leads are processed through the pipeline, their AI-drafted outreach messages will show up here."
        />
      ) : (
        <Tabs defaultValue="pending">
          <TabsList>
            <TabsTrigger value="pending">Awaiting send ({pending.length})</TabsTrigger>
            <TabsTrigger value="sent">Sent ({sent.length})</TabsTrigger>
          </TabsList>
          <TabsContent value="pending" className="space-y-3">
            {pending.length === 0 ? (
              <EmptyState icon={Mail} title="Nothing pending" description="Every drafted message has been marked as sent." />
            ) : (
              pending.map((lead) => <OutreachCard key={lead.id} lead={lead} />)
            )}
          </TabsContent>
          <TabsContent value="sent" className="space-y-3">
            {sent.length === 0 ? (
              <EmptyState icon={Mail} title="Nothing sent yet" description="Messages you've sent will appear here." />
            ) : (
              sent.map((lead) => <OutreachCard key={lead.id} lead={lead} />)
            )}
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}

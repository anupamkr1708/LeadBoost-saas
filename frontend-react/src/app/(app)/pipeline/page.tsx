"use client";

import { useState } from "react";
import { GitBranch, RotateCw, CheckCircle2, XCircle, AlertTriangle, Timer } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusDonut } from "@/components/pipeline/status-donut";
import { LatencyChart } from "@/components/pipeline/latency-chart";
import { TimeRangeSelect } from "@/components/shared/time-range-select";
import { QualificationBadge } from "@/components/shared/status-badge";
import { EmptyState } from "@/components/shared/empty-state";
import { usePipelineMetrics } from "@/features/analytics/hooks";
import { useLeads, useProcessLead } from "@/features/leads/hooks";
import { formatDate, getHostname, formatDuration } from "@/lib/utils";

export default function PipelinePage() {
  const [hours, setHours] = useState<number | undefined>(24 * 7);
  const { data: metrics, isLoading } = usePipelineMetrics(hours);
  const { data: leads, isLoading: leadsLoading } = useLeads({ limit: 20 });
  const processLead = useProcessLead();

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary-500/15 text-primary-300">
            <GitBranch className="h-5 w-5" />
          </div>
          <div>
            <h1 className="font-display text-2xl font-semibold tracking-tight">Pipeline</h1>
            <p className="text-sm text-muted-foreground">How your AI processing pipeline is performing.</p>
          </div>
        </div>
        <TimeRangeSelect value={hours} onChange={setHours} />
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {[
          { label: "Total runs", value: metrics?.total_runs, icon: GitBranch, accent: "text-primary-300 bg-primary-500/15" },
          { label: "Succeeded", value: metrics?.success_count, icon: CheckCircle2, accent: "text-emerald-300 bg-emerald-500/15" },
          { label: "Partial", value: metrics?.partial_success_count, icon: AlertTriangle, accent: "text-amber-300 bg-amber-500/15" },
          { label: "Failed", value: metrics?.failed_count, icon: XCircle, accent: "text-rose-300 bg-rose-500/15" },
        ].map((s) => (
          <Card key={s.label}>
            <CardContent className="flex items-center gap-3 p-5">
              <div className={`flex h-10 w-10 items-center justify-center rounded-xl ${s.accent}`}>
                <s.icon className="h-5 w-5" />
              </div>
              <div>
                <p className="text-xs text-muted-foreground">{s.label}</p>
                {isLoading ? <Skeleton className="mt-1 h-6 w-10" /> : <p className="font-display text-xl font-semibold">{s.value ?? 0}</p>}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Run outcomes</CardTitle>
            <CardDescription>Success rate: {metrics ? `${metrics.success_rate_pct.toFixed(1)}%` : "—"}</CardDescription>
          </CardHeader>
          <CardContent className="pt-0">{isLoading ? <Skeleton className="h-56 w-full" /> : metrics && <StatusDonut metrics={metrics} />}</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Timer className="h-4 w-4 text-muted-foreground" /> Processing time
            </CardTitle>
            <CardDescription>Average, median, and P95 latency</CardDescription>
          </CardHeader>
          <CardContent className="pt-0">{isLoading ? <Skeleton className="h-56 w-full" /> : metrics && <LatencyChart metrics={metrics} />}</CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Reprocess a lead</CardTitle>
          <CardDescription>Manually trigger the pipeline again for any of your recent leads.</CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          {leadsLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : !leads || leads.length === 0 ? (
            <EmptyState icon={GitBranch} title="No leads to process" description="Add or discover leads to see them here." />
          ) : (
            <div className="divide-y divide-white/[0.06]">
              {leads.slice(0, 8).map((lead) => (
                <div key={lead.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{lead.company_name || getHostname(lead.website)}</p>
                    <p className="text-xs text-muted-foreground">Added {formatDate(lead.created_at)}</p>
                  </div>
                  <div className="flex items-center gap-3">
                    <QualificationBadge label={lead.qualification_label} />
                    <Button
                      size="sm"
                      variant="secondary"
                      loading={processLead.isPending && processLead.variables === lead.id}
                      onClick={() => processLead.mutate(lead.id)}
                    >
                      <RotateCw className="h-3.5 w-3.5" /> Process now
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {metrics && (
        <p className="text-center text-xs text-muted-foreground">
          Based on {metrics.total_runs} pipeline run{metrics.total_runs === 1 ? "" : "s"} · avg {formatDuration(metrics.avg_processing_time_ms)}
        </p>
      )}
    </div>
  );
}

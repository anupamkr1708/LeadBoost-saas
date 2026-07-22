"use client";

import { useState } from "react";
import { BarChart3, Sparkles, Gauge, Activity } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { TimeRangeSelect } from "@/components/shared/time-range-select";
import { StatusDonut } from "@/components/pipeline/status-donut";
import { LatencyChart } from "@/components/pipeline/latency-chart";
import { DiscoveryBars } from "@/components/analytics/discovery-bars";
import { EvaluationRadar } from "@/components/analytics/evaluation-radar";
import { LeadGrowthChart } from "@/components/analytics/lead-growth-chart";
import { useDiscoveryMetrics, useEvaluationMetrics, usePipelineMetrics } from "@/features/analytics/hooks";
import { useLeads } from "@/features/leads/hooks";
import { formatDuration } from "@/lib/utils";

export default function AnalyticsPage() {
  const [hours, setHours] = useState<number | undefined>(24 * 7);
  const { data: discovery, isLoading: discoveryLoading } = useDiscoveryMetrics(hours);
  const { data: evaluation, isLoading: evaluationLoading } = useEvaluationMetrics(hours);
  const { data: pipeline, isLoading: pipelineLoading } = usePipelineMetrics(hours);
  const { data: leads, isLoading: leadsLoading } = useLeads({ limit: 300 });

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary-500/15 text-primary-300">
            <BarChart3 className="h-5 w-5" />
          </div>
          <div>
            <h1 className="font-display text-2xl font-semibold tracking-tight">Analytics</h1>
            <p className="text-sm text-muted-foreground">Performance across discovery, enrichment, and outreach.</p>
          </div>
        </div>
        <TimeRangeSelect value={hours} onChange={setHours} />
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {[
          {
            label: "Discovery success",
            value: discovery ? `${discovery.discovery_success_rate_pct.toFixed(1)}%` : "—",
            icon: Sparkles,
            loading: discoveryLoading,
          },
          {
            label: "Avg. discovery time",
            value: discovery ? formatDuration(discovery.avg_discovery_time_ms) : "—",
            icon: Activity,
            loading: discoveryLoading,
          },
          {
            label: "Avg. evaluation score",
            value: evaluation ? `${(evaluation.average_overall_score <= 1 ? evaluation.average_overall_score * 100 : evaluation.average_overall_score).toFixed(1)}` : "—",
            icon: Gauge,
            loading: evaluationLoading,
          },
          {
            label: "Pipeline success",
            value: pipeline ? `${pipeline.success_rate_pct.toFixed(1)}%` : "—",
            icon: BarChart3,
            loading: pipelineLoading,
          },
        ].map((s) => (
          <Card key={s.label}>
            <CardContent className="p-5">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <s.icon className="h-3.5 w-3.5" /> {s.label}
              </div>
              {s.loading ? <Skeleton className="mt-2 h-7 w-16" /> : <p className="mt-1.5 font-display text-2xl font-semibold">{s.value}</p>}
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Lead growth</CardTitle>
            <CardDescription>New leads created over the last 14 days</CardDescription>
          </CardHeader>
          <CardContent className="pt-0">{leadsLoading ? <Skeleton className="h-60 w-full" /> : <LeadGrowthChart leads={leads ?? []} />}</CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Discovery metrics</CardTitle>
            <CardDescription>
              {discovery ? `${discovery.total_businesses_found} businesses found · ${discovery.total_leads_created} leads created` : "—"}
            </CardDescription>
          </CardHeader>
          <CardContent className="pt-0">{discoveryLoading ? <Skeleton className="h-56 w-full" /> : discovery && <DiscoveryBars metrics={discovery} />}</CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Evaluation quality</CardTitle>
            <CardDescription>{evaluation ? `${evaluation.total_evaluations} evaluations` : "—"}</CardDescription>
          </CardHeader>
          <CardContent className="pt-0">
            {evaluationLoading ? <Skeleton className="h-60 w-full" /> : evaluation && <EvaluationRadar metrics={evaluation} />}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Pipeline outcomes</CardTitle>
            <CardDescription>{pipeline ? `${pipeline.total_runs} runs` : "—"}</CardDescription>
          </CardHeader>
          <CardContent className="pt-0">{pipelineLoading ? <Skeleton className="h-56 w-full" /> : pipeline && <StatusDonut metrics={pipeline} />}</CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Processing latency</CardTitle>
            <CardDescription>Average, median, and P95 pipeline processing time</CardDescription>
          </CardHeader>
          <CardContent className="pt-0">{pipelineLoading ? <Skeleton className="h-56 w-full" /> : pipeline && <LatencyChart metrics={pipeline} />}</CardContent>
        </Card>
      </div>
    </div>
  );
}

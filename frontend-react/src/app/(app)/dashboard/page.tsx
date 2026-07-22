"use client";

import Link from "next/link";
import { Users, Sparkles, Target, Gauge, Activity, Zap, ArrowRight } from "lucide-react";
import { KpiCard } from "@/components/dashboard/kpi-card";
import { QualificationChart } from "@/components/dashboard/qualification-chart";
import { ActivityFeed } from "@/components/dashboard/activity-feed";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Progress } from "@/components/ui/progress";
import { Button } from "@/components/ui/button";
import { useLeads } from "@/features/leads/hooks";
import { useUsage } from "@/features/billing/hooks";
import { useDiscoveryMetrics, usePipelineMetrics } from "@/features/analytics/hooks";
import { useAuthStore } from "@/store/auth-store";
import { formatPercent } from "@/lib/utils";

export default function DashboardPage() {
  const user = useAuthStore((s) => s.user);
  const { data: leads, isLoading: leadsLoading } = useLeads({ limit: 100 });
  const { data: usage, isLoading: usageLoading } = useUsage();
  const { data: discoveryMetrics, isLoading: discoveryLoading } = useDiscoveryMetrics(24 * 7);
  const { data: pipelineMetrics, isLoading: pipelineLoading } = usePipelineMetrics(24 * 7);

  const today = new Date().toDateString();
  const todaysLeads = leads?.filter((l) => new Date(l.created_at).toDateString() === today).length ?? 0;
  const qualifiedLeads =
    leads?.filter((l) => ["qualified", "hot", "warm"].includes((l.qualification_label || "").toLowerCase())).length ?? 0;
  const avgScore = leads && leads.length > 0 ? leads.reduce((sum, l) => sum + (l.score || 0), 0) / leads.length : 0;

  return (
    <div className="space-y-8">
      <div className="flex flex-col gap-1">
        <h1 className="font-display text-2xl font-semibold tracking-tight sm:text-3xl">
          Welcome back{user?.first_name ? `, ${user.first_name}` : ""}
        </h1>
        <p className="text-sm text-muted-foreground">Here&apos;s how your pipeline is performing.</p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <KpiCard label="Today's leads" value={todaysLeads} icon={Users} accent="primary" loading={leadsLoading} />
        <KpiCard
          label="Discovery success"
          value={discoveryMetrics?.discovery_success_rate_pct ?? 0}
          suffix="%"
          decimals={1}
          icon={Sparkles}
          accent="accent"
          loading={discoveryLoading}
        />
        <KpiCard label="Qualified leads" value={qualifiedLeads} icon={Target} accent="success" loading={leadsLoading} />
        <KpiCard label="Avg. lead score" value={avgScore} decimals={1} icon={Gauge} accent="warning" loading={leadsLoading} />
        <KpiCard
          label="AI processing success"
          value={pipelineMetrics?.success_rate_pct ?? 0}
          suffix="%"
          decimals={1}
          icon={Activity}
          accent="primary"
          loading={pipelineLoading}
        />
        <KpiCard
          label="Usage remaining today"
          value={usage?.remaining_daily_leads ?? 0}
          icon={Zap}
          accent="accent"
          loading={usageLoading}
        />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="flex flex-row items-center justify-between">
            <div>
              <CardTitle>Latest discoveries</CardTitle>
              <CardDescription>Your most recently created leads</CardDescription>
            </div>
            <Button variant="ghost" size="sm" asChild>
              <Link href="/leads">
                View all <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </Button>
          </CardHeader>
          <CardContent className="pt-0">
            {leadsLoading ? (
              <div className="space-y-3">
                {Array.from({ length: 5 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            ) : (
              <ActivityFeed leads={(leads ?? []).slice(0, 6)} />
            )}
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Qualification mix</CardTitle>
              <CardDescription>Across your current leads</CardDescription>
            </CardHeader>
            <CardContent className="pt-0">
              {leadsLoading ? <Skeleton className="h-56 w-full" /> : <QualificationChart leads={leads ?? []} />}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Plan usage</CardTitle>
              <CardDescription>{usage?.plan_name ?? "—"} plan</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 pt-0">
              {usageLoading ? (
                <Skeleton className="h-8 w-full" />
              ) : usage ? (
                <>
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">
                      {usage.current_usage} / {usage.max_leads_per_day} leads today
                    </span>
                    <span className="font-mono text-xs">
                      {formatPercent(usage.current_usage / Math.max(usage.max_leads_per_day, 1))}
                    </span>
                  </div>
                  <Progress value={(usage.current_usage / Math.max(usage.max_leads_per_day, 1)) * 100} />
                  <Button variant="secondary" size="sm" className="w-full" asChild>
                    <Link href="/billing">Manage plan</Link>
                  </Button>
                </>
              ) : null}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

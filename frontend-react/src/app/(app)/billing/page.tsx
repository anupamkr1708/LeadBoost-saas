"use client";

import { useState } from "react";
import { CreditCard, Zap, FileDown, Sparkles } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmDialog } from "@/components/shared/confirm-dialog";
import { PlanCard } from "@/components/billing/plan-card";
import { useUsage, usePlans, useUpgradePlan, useCancelSubscription } from "@/features/billing/hooks";
import type { PlanOption } from "@/types/api";

export default function BillingPage() {
  const { data: usage, isLoading: usageLoading } = useUsage();
  const { data: plansData, isLoading: plansLoading } = usePlans();
  const upgrade = useUpgradePlan();
  const cancelSub = useCancelSubscription();
  const [cancelOpen, setCancelOpen] = useState(false);
  const [immediate, setImmediate] = useState(false);

  const plans: PlanOption[] = Array.isArray(plansData) ? plansData : plansData ? Object.values(plansData) : [];

  return (
    <div className="space-y-8">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary-500/15 text-primary-300">
          <CreditCard className="h-5 w-5" />
        </div>
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight">Billing</h1>
          <p className="text-sm text-muted-foreground">Manage your plan, usage, and subscription.</p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{usage?.plan_name ?? "—"} plan</CardTitle>
          <CardDescription>Your organization&apos;s current usage and capabilities</CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          {usageLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : usage ? (
            <div className="space-y-5">
              <div>
                <div className="flex items-center justify-between text-sm">
                  <span className="text-muted-foreground">
                    {usage.current_usage} / {usage.max_leads_per_day} leads used today
                  </span>
                  <span className="font-mono text-xs">{usage.remaining_daily_leads} remaining</span>
                </div>
                <Progress value={(usage.current_usage / Math.max(usage.max_leads_per_day, 1)) * 100} className="mt-2" />
              </div>
              <div className="grid grid-cols-3 gap-3">
                <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3 text-center">
                  <Zap className="mx-auto h-4 w-4 text-primary-400" />
                  <p className="mt-1.5 text-xs text-muted-foreground">Can process more today</p>
                  <p className="mt-0.5 text-sm font-medium">{usage.can_process_more_today ? "Yes" : "No"}</p>
                </div>
                <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3 text-center">
                  <Sparkles className="mx-auto h-4 w-4 text-primary-400" />
                  <p className="mt-1.5 text-xs text-muted-foreground">AI processing</p>
                  <p className="mt-0.5 text-sm font-medium">{usage.can_use_ai ? "Enabled" : "Disabled"}</p>
                </div>
                <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3 text-center">
                  <FileDown className="mx-auto h-4 w-4 text-primary-400" />
                  <p className="mt-1.5 text-xs text-muted-foreground">Export</p>
                  <p className="mt-0.5 text-sm font-medium">{usage.can_export ? "Enabled" : "Disabled"}</p>
                </div>
              </div>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <div>
        <h2 className="mb-4 font-display text-lg font-semibold">Available plans</h2>
        {plansLoading ? (
          <div className="grid gap-5 sm:grid-cols-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-72 w-full" />
            ))}
          </div>
        ) : plans.length === 0 ? (
          <p className="text-sm text-muted-foreground">No plans are currently available from the API.</p>
        ) : (
          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {plans.map((plan, i) => (
              <PlanCard
                key={plan.name ?? plan.plan_name ?? i}
                plan={plan}
                currentPlanName={usage?.plan_name}
                onUpgrade={(name) => upgrade.mutate(name)}
                upgrading={upgrade.isPending}
              />
            ))}
          </div>
        )}
      </div>

      <Card className="border-rose-500/20">
        <CardHeader>
          <CardTitle>Cancel subscription</CardTitle>
          <CardDescription>This downgrades your organization once the action is confirmed.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center justify-between gap-4 pt-0">
          <div className="flex items-center gap-3">
            <Switch id="immediate" checked={immediate} onCheckedChange={setImmediate} />
            <Label htmlFor="immediate" className="text-foreground">
              Cancel immediately (instead of at period end)
            </Label>
          </div>
          <Button variant="destructive" onClick={() => setCancelOpen(true)}>
            Cancel subscription
          </Button>
        </CardContent>
      </Card>

      <ConfirmDialog
        open={cancelOpen}
        onOpenChange={setCancelOpen}
        title="Cancel your subscription?"
        description={immediate ? "Your plan will be canceled immediately." : "Your plan will remain active until the end of the current billing period."}
        confirmLabel="Confirm cancellation"
        destructive
        loading={cancelSub.isPending}
        onConfirm={() => {
          cancelSub.mutate(immediate);
          setCancelOpen(false);
        }}
      />
    </div>
  );
}

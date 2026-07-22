"use client";

import { Check, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { PlanOption } from "@/types/api";

interface PlanCardProps {
  plan: PlanOption;
  currentPlanName?: string;
  onUpgrade: (planName: string) => void;
  upgrading: boolean;
}

export function PlanCard({ plan, currentPlanName, onUpgrade, upgrading }: PlanCardProps) {
  const name = plan.name ?? plan.plan_name ?? "Plan";
  const isCurrent = currentPlanName?.toLowerCase() === name.toLowerCase();
  const price = plan.price ?? plan.price_monthly;
  const features = Array.isArray(plan.features) ? plan.features : [];

  return (
    <div className={cn("glass flex flex-col p-6", isCurrent && "border-primary-500/40 shadow-glow")}>
      <div className="flex items-center justify-between">
        <h3 className="font-display text-lg font-semibold">{name}</h3>
        {isCurrent && (
          <span className="flex items-center gap-1 rounded-full bg-primary-500/15 px-2.5 py-0.5 text-xs font-medium text-primary-300">
            <Sparkles className="h-3 w-3" /> Current
          </span>
        )}
      </div>
      {price !== undefined && (
        <div className="mt-2 flex items-baseline gap-1">
          <span className="font-display text-3xl font-semibold tracking-tight">{typeof price === "number" ? `$${price}` : price}</span>
          <span className="text-sm text-muted-foreground">/mo</span>
        </div>
      )}
      {typeof plan.max_leads_per_day === "number" && (
        <p className="mt-2 text-sm text-muted-foreground">{plan.max_leads_per_day} leads / day</p>
      )}
      {features.length > 0 && (
        <ul className="mt-4 flex-1 space-y-2">
          {features.map((f) => (
            <li key={f} className="flex items-start gap-2 text-sm">
              <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" /> {f}
            </li>
          ))}
        </ul>
      )}
      <Button className="mt-6 w-full" variant={isCurrent ? "secondary" : "default"} disabled={isCurrent} loading={upgrading} onClick={() => onUpgrade(name)}>
        {isCurrent ? "Current plan" : "Upgrade"}
      </Button>
    </div>
  );
}

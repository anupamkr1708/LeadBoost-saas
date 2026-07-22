"use client";

import type { LucideIcon } from "lucide-react";
import { motion } from "framer-motion";
import { AnimatedCounter } from "@/components/shared/animated-counter";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface KpiCardProps {
  label: string;
  value: number;
  suffix?: string;
  decimals?: number;
  compact?: boolean;
  icon: LucideIcon;
  trend?: { value: number; positive: boolean };
  accent?: "primary" | "success" | "warning" | "danger" | "accent";
  loading?: boolean;
}

const accentMap: Record<NonNullable<KpiCardProps["accent"]>, string> = {
  primary: "from-primary-500/20 to-primary-500/0 text-primary-300",
  success: "from-emerald-500/20 to-emerald-500/0 text-emerald-300",
  warning: "from-amber-500/20 to-amber-500/0 text-amber-300",
  danger: "from-rose-500/20 to-rose-500/0 text-rose-300",
  accent: "from-accent/20 to-accent/0 text-accent",
};

/** The primary KPI tile used across the dashboard grid. */
export function KpiCard({ label, value, suffix, decimals, compact, icon: Icon, trend, accent = "primary", loading }: KpiCardProps) {
  if (loading) {
    return (
      <Card>
        <div className="p-6">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="mt-4 h-8 w-20" />
        </div>
      </Card>
    );
  }

  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
      <Card className="overflow-hidden">
        <div className="flex items-start justify-between p-6">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
            <p className="mt-3 font-display text-3xl font-semibold tracking-tight">
              <AnimatedCounter value={value} suffix={suffix} decimals={decimals} compact={compact} />
            </p>
            {trend && (
              <p className={cn("mt-2 text-xs font-medium", trend.positive ? "text-emerald-400" : "text-rose-400")}>
                {trend.positive ? "▲" : "▼"} {Math.abs(trend.value)}% vs. previous period
              </p>
            )}
          </div>
          <div className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br", accentMap[accent])}>
            <Icon className="h-5 w-5" />
          </div>
        </div>
      </Card>
    </motion.div>
  );
}

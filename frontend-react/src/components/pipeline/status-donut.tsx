"use client";

import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend } from "recharts";
import type { PipelineMetricsSummary } from "@/types/api";

interface StatusDonutProps {
  metrics: PipelineMetricsSummary;
}

export function StatusDonut({ metrics }: StatusDonutProps) {
  const data = [
    { name: "Success", value: metrics.success_count, color: "#10B981" },
    { name: "Partial", value: metrics.partial_success_count, color: "#F59E0B" },
    { name: "Failed", value: metrics.failed_count, color: "#F43F5E" },
  ].filter((d) => d.value > 0);

  if (data.length === 0) {
    return <div className="flex h-56 items-center justify-center text-sm text-muted-foreground">No pipeline runs in this window</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={220}>
      <PieChart>
        <Pie data={data} dataKey="value" nameKey="name" innerRadius={55} outerRadius={82} paddingAngle={3}>
          {data.map((d) => (
            <Cell key={d.name} fill={d.color} stroke="rgba(0,0,0,0.2)" />
          ))}
        </Pie>
        <Tooltip contentStyle={{ background: "rgba(18,16,28,0.95)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 12, fontSize: 12 }} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
      </PieChart>
    </ResponsiveContainer>
  );
}

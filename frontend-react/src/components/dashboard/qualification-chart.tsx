"use client";

import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend } from "recharts";
import type { Lead } from "@/types/api";
import { QUALIFICATION_STYLES } from "@/lib/constants";

const COLORS: Record<string, string> = {
  hot: "#F43F5E",
  warm: "#F59E0B",
  cold: "#38BDF8",
  qualified: "#10B981",
  unqualified: "#8B8794",
};

interface QualificationChartProps {
  leads: Lead[];
}

/** Donut chart summarizing the current qualification-label distribution across leads. */
export function QualificationChart({ leads }: QualificationChartProps) {
  const counts = new Map<string, number>();
  leads.forEach((l) => {
    const key = (l.qualification_label || "unqualified").toLowerCase();
    counts.set(key, (counts.get(key) ?? 0) + 1);
  });
  const data = Array.from(counts.entries()).map(([key, value]) => ({
    name: QUALIFICATION_STYLES[key]?.label ?? key,
    key,
    value,
  }));

  if (data.length === 0) {
    return <div className="flex h-56 items-center justify-center text-sm text-muted-foreground">No leads yet</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={220}>
      <PieChart>
        <Pie data={data} dataKey="value" nameKey="name" innerRadius={55} outerRadius={82} paddingAngle={3}>
          {data.map((d) => (
            <Cell key={d.key} fill={COLORS[d.key] ?? "#8B8794"} stroke="rgba(0,0,0,0.2)" />
          ))}
        </Pie>
        <Tooltip
          contentStyle={{
            background: "rgba(18,16,28,0.95)",
            border: "1px solid rgba(255,255,255,0.1)",
            borderRadius: 12,
            fontSize: 12,
          }}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
      </PieChart>
    </ResponsiveContainer>
  );
}

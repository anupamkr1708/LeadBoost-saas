"use client";

import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend } from "recharts";
import type { Lead } from "@/types/api";
import { QUALIFICATION_STYLES } from "@/lib/constants";

// P1.2: keyed by the backend's actual qualification_label values
// (lowercased) — "Hot Lead"/"Warm Lead"/"Cold Lead"/"Disqualified" — not
// the shorter "hot"/"warm"/"cold"/"qualified" this map previously used,
// which meant every real label fell through to the grey fallback color
// below. Same underlying vocabulary mismatch as QUALIFICATION_STYLES in
// lib/constants.ts; kept as a separate map here only because chart slice
// colors are a slightly different concern from badge className, not
// because the keys should ever diverge again.
const COLORS: Record<string, string> = {
  "hot lead": "#F43F5E",
  "warm lead": "#F59E0B",
  "cold lead": "#38BDF8",
  disqualified: "#8B8794",
};

interface QualificationChartProps {
  leads: Lead[];
}

/** Donut chart summarizing the current qualification-label distribution across leads. */
export function QualificationChart({ leads }: QualificationChartProps) {
  const counts = new Map<string, number>();
  leads.forEach((l) => {
    const key = (l.qualification_label || "").toLowerCase();
    counts.set(key, (counts.get(key) ?? 0) + 1);
  });
  const data = Array.from(counts.entries()).map(([key, value]) => ({
    name: QUALIFICATION_STYLES[key]?.label ?? (key || "Unscored"),
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

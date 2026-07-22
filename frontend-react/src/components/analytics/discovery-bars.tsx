"use client";

import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip, CartesianGrid, Cell } from "recharts";
import type { DiscoveryMetricsSummary } from "@/types/api";

export function DiscoveryBars({ metrics }: { metrics: DiscoveryMetricsSummary }) {
  const data = [
    { name: "Discovery success", value: Math.round(metrics.discovery_success_rate_pct * 10) / 10, color: "#8B5CF6" },
    { name: "Website resolution", value: Math.round(metrics.website_resolution_rate_pct * 10) / 10, color: "#38BDF8" },
    { name: "Duplicate removal", value: Math.round(metrics.duplicate_removal_rate_pct * 10) / 10, color: "#D946EF" },
  ];

  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} layout="vertical" margin={{ left: 20 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" horizontal={false} />
        <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 11, fill: "#A3A0B8" }} axisLine={false} tickLine={false} />
        <YAxis type="category" dataKey="name" width={130} tick={{ fontSize: 12, fill: "#A3A0B8" }} axisLine={false} tickLine={false} />
        <Tooltip
          contentStyle={{ background: "rgba(18,16,28,0.95)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 12, fontSize: 12 }}
          formatter={(value: number) => [`${value}%`, ""]}
        />
        <Bar dataKey="value" radius={[0, 8, 8, 0]}>
          {data.map((d) => (
            <Cell key={d.name} fill={d.color} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

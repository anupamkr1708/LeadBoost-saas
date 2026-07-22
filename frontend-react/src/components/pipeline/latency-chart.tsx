"use client";

import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip, CartesianGrid } from "recharts";
import type { PipelineMetricsSummary } from "@/types/api";

export function LatencyChart({ metrics }: { metrics: PipelineMetricsSummary }) {
  const data = [
    { name: "Avg", ms: Math.round(metrics.avg_processing_time_ms) },
    { name: "Median", ms: Math.round(metrics.median_processing_time_ms) },
    { name: "P95", ms: Math.round(metrics.p95_processing_time_ms) },
  ];

  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ left: -20 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" vertical={false} />
        <XAxis dataKey="name" tick={{ fontSize: 12, fill: "#A3A0B8" }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fontSize: 11, fill: "#A3A0B8" }} axisLine={false} tickLine={false} />
        <Tooltip
          contentStyle={{ background: "rgba(18,16,28,0.95)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 12, fontSize: 12 }}
          formatter={(value: number) => [`${value}ms`, "Processing time"]}
        />
        <Bar dataKey="ms" radius={[8, 8, 0, 0]} fill="url(#pipelineBarGradient)" />
        <defs>
          <linearGradient id="pipelineBarGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#8B5CF6" />
            <stop offset="100%" stopColor="#38BDF8" />
          </linearGradient>
        </defs>
      </BarChart>
    </ResponsiveContainer>
  );
}

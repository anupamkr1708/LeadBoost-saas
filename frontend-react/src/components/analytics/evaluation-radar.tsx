"use client";

import { Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer, Tooltip } from "recharts";
import type { EvaluationMetricsSummary } from "@/types/api";

export function EvaluationRadar({ metrics }: { metrics: EvaluationMetricsSummary }) {
  const toPct = (v: number) => Math.round((v <= 1 ? v * 100 : v) * 10) / 10;
  const data = [
    { metric: "Confidence", value: toPct(metrics.average_confidence) },
    { metric: "Completeness", value: toPct(metrics.average_completeness) },
    { metric: "Grounding", value: toPct(metrics.average_grounding) },
    { metric: "Consistency", value: toPct(metrics.average_consistency) },
  ];

  return (
    <ResponsiveContainer width="100%" height={260}>
      <RadarChart data={data} outerRadius="75%">
        <PolarGrid stroke="rgba(255,255,255,0.1)" />
        <PolarAngleAxis dataKey="metric" tick={{ fontSize: 12, fill: "#A3A0B8" }} />
        <PolarRadiusAxis domain={[0, 100]} tick={{ fontSize: 10, fill: "#A3A0B8" }} axisLine={false} />
        <Radar dataKey="value" stroke="#8B5CF6" fill="#8B5CF6" fillOpacity={0.35} />
        <Tooltip contentStyle={{ background: "rgba(18,16,28,0.95)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 12, fontSize: 12 }} />
      </RadarChart>
    </ResponsiveContainer>
  );
}

"use client";

import { AreaChart, Area, XAxis, YAxis, ResponsiveContainer, Tooltip, CartesianGrid } from "recharts";
import type { Lead } from "@/types/api";

/** Buckets leads by creation day (last 14 days) to visualize growth momentum. */
export function LeadGrowthChart({ leads }: { leads: Lead[] }) {
  const days = 14;
  const buckets: { date: string; count: number }[] = [];
  const now = new Date();

  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    const key = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
    const count = leads.filter((l) => new Date(l.created_at).toDateString() === d.toDateString()).length;
    buckets.push({ date: key, count });
  }

  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={buckets} margin={{ left: -20 }}>
        <defs>
          <linearGradient id="growthGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#8B5CF6" stopOpacity={0.5} />
            <stop offset="100%" stopColor="#8B5CF6" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" vertical={false} />
        <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#A3A0B8" }} axisLine={false} tickLine={false} interval={1} />
        <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "#A3A0B8" }} axisLine={false} tickLine={false} />
        <Tooltip contentStyle={{ background: "rgba(18,16,28,0.95)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 12, fontSize: 12 }} />
        <Area type="monotone" dataKey="count" stroke="#8B5CF6" strokeWidth={2} fill="url(#growthGradient)" />
      </AreaChart>
    </ResponsiveContainer>
  );
}

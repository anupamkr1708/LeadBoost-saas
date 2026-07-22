"use client";

import { cn } from "@/lib/utils";

interface ScoreRingProps {
  value: number; // 0-1 or 0-100
  size?: number;
  strokeWidth?: number;
  label?: string;
  className?: string;
}

/** A compact circular indicator for confidence / lead scores. */
export function ScoreRing({ value, size = 56, strokeWidth = 5, label, className }: ScoreRingProps) {
  const pct = value <= 1 ? value * 100 : value;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (pct / 100) * circumference;

  const color = pct >= 70 ? "#10B981" : pct >= 40 ? "#F59E0B" : "#F43F5E";

  return (
    <div className={cn("relative inline-flex items-center justify-center", className)} style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={radius} stroke="rgba(255,255,255,0.08)" strokeWidth={strokeWidth} fill="none" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          stroke={color}
          strokeWidth={strokeWidth}
          fill="none"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          style={{ transition: "stroke-dashoffset 0.6s ease" }}
        />
      </svg>
      <span className="absolute font-mono text-xs font-semibold">{label ?? `${Math.round(pct)}`}</span>
    </div>
  );
}

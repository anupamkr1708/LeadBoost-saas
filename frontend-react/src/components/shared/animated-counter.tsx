"use client";

import { useEffect, useRef } from "react";
import { animate, useMotionValue, useTransform } from "framer-motion";
import { formatCompactNumber } from "@/lib/utils";

interface AnimatedCounterProps {
  value: number;
  compact?: boolean;
  suffix?: string;
  decimals?: number;
}

/** Animates a KPI number counting up from its previous value whenever `value` changes. */
export function AnimatedCounter({ value, compact = false, suffix = "", decimals = 0 }: AnimatedCounterProps) {
  const motionValue = useMotionValue(0);
  const ref = useRef<HTMLSpanElement>(null);
  const rounded = useTransform(motionValue, (latest) =>
    compact ? formatCompactNumber(latest) : latest.toFixed(decimals)
  );

  useEffect(() => {
    const controls = animate(motionValue, value, { duration: 0.9, ease: [0.16, 1, 0.3, 1] });
    return controls.stop;
  }, [value, motionValue]);

  useEffect(() => {
    return rounded.on("change", (latest) => {
      if (ref.current) ref.current.textContent = `${latest}${suffix}`;
    });
  }, [rounded, suffix]);

  return <span ref={ref} className="font-mono tabular-nums">0{suffix}</span>;
}

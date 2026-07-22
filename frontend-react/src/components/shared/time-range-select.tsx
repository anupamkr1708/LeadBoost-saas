"use client";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { TIME_RANGE_OPTIONS } from "@/lib/constants";

interface TimeRangeSelectProps {
  value: number | undefined;
  onChange: (hours: number | undefined) => void;
}

/** Shared time-window selector for analytics/pipeline pages, backed by each endpoint's `hours` param. */
export function TimeRangeSelect({ value, onChange }: TimeRangeSelectProps) {
  return (
    <Select
      value={value === undefined ? "all" : String(value)}
      onValueChange={(v) => onChange(v === "all" ? undefined : Number(v))}
    >
      <SelectTrigger className="w-36">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {TIME_RANGE_OPTIONS.map((opt) => (
          <SelectItem key={opt.label} value={opt.hours === undefined ? "all" : String(opt.hours)}>
            {opt.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

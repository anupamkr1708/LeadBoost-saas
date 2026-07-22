import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merge Tailwind class names safely, resolving conflicting utility classes.
 * Provenance: standard shadcn/ui `cn` helper pattern (clsx + tailwind-merge).
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Format a number as a compact, locale-aware string (1.2k, 3.4M). */
export function formatCompactNumber(value: number): string {
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

/** Format a 0-1 or 0-100 score as a percentage string, tolerant of either scale. */
export function formatPercent(value: number, alreadyPct = false): string {
  const pct = alreadyPct ? value : value * 100;
  return `${pct.toFixed(pct % 1 === 0 ? 0 : 1)}%`;
}

/** Format an ISO date-time string as a short, human-readable date. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(new Date(iso));
  } catch {
    return "—";
  }
}

/** Format an ISO date-time string as a short date + time. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }).format(new Date(iso));
  } catch {
    return "—";
  }
}

/** Format milliseconds into a compact human duration (e.g. 850ms, 2.3s). */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** Extract initials (up to 2 letters) from a name or email, for avatar fallbacks. */
export function getInitials(name?: string | null, email?: string | null): string {
  if (name && name.trim()) {
    const parts = name.trim().split(/\s+/);
    return parts.slice(0, 2).map((p) => p[0]?.toUpperCase() ?? "").join("");
  }
  if (email) return email[0]?.toUpperCase() ?? "?";
  return "?";
}

/** Derive a bare hostname from a URL for compact display. */
export function getHostname(url: string): string {
  try {
    const withProtocol = url.startsWith("http") ? url : `https://${url}`;
    return new URL(withProtocol).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Ensure a URL has a protocol, so it can be safely opened in a new tab. */
export function ensureProtocol(url: string): string {
  return url.startsWith("http://") || url.startsWith("https://") ? url : `https://${url}`;
}

/** Clamp a number between a min and max. */
export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/**
 * App-wide constants.
 * Provenance: derived from the LeadBoost OpenAPI spec (v2.0.0) and product brief.
 */

export const APP_NAME = "LeadBoost";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** localStorage keys for the persisted auth session. */
export const AUTH_STORAGE_KEY = "leadboost-auth";

/** Qualification labels as produced by the backend scoring pipeline (display-only mapping). */
export const QUALIFICATION_STYLES: Record<string, { label: string; className: string }> = {
  hot: { label: "Hot", className: "bg-rose-500/15 text-rose-300 border-rose-500/30" },
  warm: { label: "Warm", className: "bg-amber-500/15 text-amber-300 border-amber-500/30" },
  cold: { label: "Cold", className: "bg-sky-500/15 text-sky-300 border-sky-500/30" },
  qualified: { label: "Qualified", className: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30" },
  unqualified: { label: "Unqualified", className: "bg-white/10 text-muted-foreground border-white/10" },
};

export const DEFAULT_QUALIFICATION_STYLE = {
  label: "Unscored",
  className: "bg-white/10 text-muted-foreground border-white/10",
};

/** Pipeline / discovery status → visual treatment. */
export const STATUS_STYLES: Record<string, { label: string; className: string; dot: string }> = {
  SUCCESS: { label: "Success", className: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30", dot: "bg-emerald-400" },
  PARTIAL_SUCCESS: { label: "Partial", className: "bg-amber-500/15 text-amber-300 border-amber-500/30", dot: "bg-amber-400" },
  FAILED: { label: "Failed", className: "bg-rose-500/15 text-rose-300 border-rose-500/30", dot: "bg-rose-400" },
  CREATED: { label: "Created", className: "bg-sky-500/15 text-sky-300 border-sky-500/30", dot: "bg-sky-400" },
  DUPLICATE: { label: "Duplicate", className: "bg-white/10 text-muted-foreground border-white/10", dot: "bg-white/40" },
  SKIPPED: { label: "Skipped", className: "bg-white/10 text-muted-foreground border-white/10", dot: "bg-white/40" },
  QUEUED: { label: "Queued", className: "bg-indigo-500/15 text-indigo-300 border-indigo-500/30", dot: "bg-indigo-400" },
  RUNNING: { label: "Running", className: "bg-primary-500/15 text-primary-300 border-primary-500/30", dot: "bg-primary-400" },
  // Decision agent's recommended_action (application/dto/models.py::DecisionOutput)
  PROCEED: { label: "Proceed", className: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30", dot: "bg-emerald-400" },
  REVIEW: { label: "Review", className: "bg-amber-500/15 text-amber-300 border-amber-500/30", dot: "bg-amber-400" },
  REJECT: { label: "Reject", className: "bg-rose-500/15 text-rose-300 border-rose-500/30", dot: "bg-rose-400" },
  // Review agent's routing decision (application/dto/models.py::ReviewOutput)
  AUTO_APPROVED: { label: "Auto-approved", className: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30", dot: "bg-emerald-400" },
  FLAGGED: { label: "Flagged", className: "bg-amber-500/15 text-amber-300 border-amber-500/30", dot: "bg-amber-400" },
  HUMAN_REVIEW: { label: "Human review", className: "bg-indigo-500/15 text-indigo-300 border-indigo-500/30", dot: "bg-indigo-400" },
};

export const TIME_RANGE_OPTIONS = [
  { label: "24h", hours: 24 },
  { label: "7d", hours: 24 * 7 },
  { label: "30d", hours: 24 * 30 },
  { label: "All time", hours: undefined },
] as const;

/** Default timeout for fast CRUD-style endpoints (leads, org, billing, auth). */
export const DEFAULT_TIMEOUT_MS = 30_000;

/**
 * Discovery search runs synchronously on the backend (resolve → enrich → score
 * → create leads) and can legitimately take minutes, not seconds — see
 * application.workflow stage_span logs. Give it a much longer budget than
 * everything else instead of raising the global default.
 */
export const DISCOVERY_TIMEOUT_MS = 6 * 60 * 1000; // 6 minutes

export const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard", icon: "LayoutDashboard" },
  { href: "/discovery", label: "Lead Discovery", icon: "Sparkles" },
  { href: "/leads", label: "Leads", icon: "Users" },
  { href: "/pipeline", label: "Pipeline", icon: "GitBranch" },
  { href: "/outreach", label: "Outreach", icon: "Mail" },
  { href: "/analytics", label: "Analytics", icon: "BarChart3" },
  { href: "/organization", label: "Organization", icon: "Building2" },
  { href: "/billing", label: "Billing", icon: "CreditCard" },
  { href: "/profile", label: "Profile", icon: "UserCircle" },
  { href: "/settings", label: "Settings", icon: "Settings" },
] as const;

/**
 * Types generated directly from `LeadBoost SaaS API` OpenAPI schema (v2.0.0).
 * Provenance: components.schemas in the supplied openapi.json. Do not hand-edit
 * shapes here without updating the source spec — this is the single source of truth
 * the frontend types itself against.
 */

// ---------- Auth ----------

export interface User {
  email: string;
  first_name: string | null;
  last_name: string | null;
  id: number;
  is_active: boolean;
  is_verified: boolean;
  organization_id: number | null;
  created_at: string;
  updated_at: string | null;
}

export interface UserCreate {
  email: string;
  first_name?: string | null;
  last_name?: string | null;
  password: string;
}

export interface UserUpdate {
  first_name?: string | null;
  last_name?: string | null;
  is_active?: boolean | null;
}

export interface LoginResponse {
  access_token: string;
  refresh_token?: string;
  token_type?: string;
  [key: string]: unknown;
}

export interface RefreshResponse {
  access_token: string;
  refresh_token?: string;
  token_type?: string;
  [key: string]: unknown;
}

// ---------- Leads ----------

export interface Lead {
  website: string;
  organization_id: number;
  owner_id: number;
  id: number;
  company_name: string | null;
  industry: string | null;
  about_text: string | null;
  contact_name: string | null;
  contact_title: string | null;
  email: string | null;
  phone: string | null;
  address: string | null;
  linkedin_url: string | null;
  twitter_url: string | null;
  facebook_url: string | null;
  employees: string | null;
  revenue_band: string | null;
  founded_year: number | null;
  score: number;
  qualification_label: string;
  scrape_confidence: number;
  email_confidence: number;
  enrichment_confidence: number;
  enrichment_source: string;
  email_source: string;
  scrape_source: string;
  outreach_message: string | null;
  outreach_sent: boolean;
  outreach_sent_at: string | null;
  is_active: boolean;
  is_verified: boolean;
  created_at: string;
  updated_at: string | null;
}

export interface LeadCreate {
  website: string;
  organization_id: number;
  owner_id: number;
}

// ---------- Lead AI insights (GET /leads/{id} only) ----------
//
// Backend provenance: application/services/infra_adapters.py::get_lead_ai_insights,
// reconstructing each stage's own DTO from application/dto/models.py
// (CompanyIntelligenceOutput / DecisionOutput / EvaluationReport /
// ReviewOutput / MessagingOutput). Every stage is `null` until the pipeline
// has actually run that stage for this lead (e.g. `messaging` stays null
// when the lead was routed to human_review).

export interface AIExplanation {
  reasoning: string;
  evidence: string[];
  confidence: number;
}

export interface CompanyIntelligenceInsight {
  industry_analysis: string | null;
  website_quality: string | null;
  technology_signals: string[];
  market_position: string | null;
  pain_points: string[];
  growth_indicators: string[];
  icp_alignment_score: number;
  explanation: AIExplanation;
  source: "heuristic" | "llm" | string;
  model_used: string | null;
  generated_at: string | null;
}

export interface DecisionInsight {
  qualification: string;
  recommended_action: "proceed" | "review" | "reject" | string;
  explanation: AIExplanation;
  source: "rule_based" | "llm" | string;
  model_used: string | null;
  generated_at: string | null;
}

export interface EvaluationInsight {
  confidence: number;
  completeness: number;
  grounding: number;
  consistency: number;
  overall: number;
  notes: string[];
  model_used: string | null;
  generated_at: string | null;
}

export interface ReviewInsight {
  decision: "auto_approved" | "flagged" | "human_review" | string;
  reason: string;
  threshold_used: number | null;
  model_used: string | null;
  generated_at: string | null;
}

export interface MessagingInsight {
  email_subject: string | null;
  email_body: string | null;
  linkedin_opener: string | null;
  follow_up_strategy: string | null;
  channel_notes: string | null;
  explanation: AIExplanation;
  source: "template" | "llm" | string;
  model_used: string | null;
  generated_at: string | null;
}

export interface LeadAIInsights {
  company_intelligence: CompanyIntelligenceInsight | null;
  decision: DecisionInsight | null;
  evaluation: EvaluationInsight | null;
  review: ReviewInsight | null;
  messaging: MessagingInsight | null;
}

/** Response shape of GET /leads/{id} specifically — every `Lead` field plus
 * the AI insights bundle that only that endpoint returns (list/create/update
 * responses are the plain `Lead` shape and do not include it). */
export interface LeadDetail extends Lead {
  ai_insights: LeadAIInsights;
}


export interface LeadUpdate {
  company_name?: string | null;
  industry?: string | null;
  about_text?: string | null;
  contact_name?: string | null;
  contact_title?: string | null;
  email?: string | null;
  phone?: string | null;
  address?: string | null;
  linkedin_url?: string | null;
  twitter_url?: string | null;
  facebook_url?: string | null;
  employees?: string | null;
  score?: number | null;
  qualification_label?: string | null;
  scrape_confidence?: number | null;
  scrape_source?: string | null;
  enrichment_confidence?: number | null;
  enrichment_source?: string | null;
  revenue_band?: string | null;
  founded_year?: number | null;
  outreach_message?: string | null;
  is_active?: boolean | null;
}

export interface LeadProcessRequest {
  urls: string[];
  message_style?: string;
}

// ---------- Discovery ----------

export interface LeadCreationOutcome {
  name: string;
  website: string | null;
  status: string;
  lead_id: number | null;
  pipeline_status: string | null;
  reason: string | null;
}

export interface DiscoverySearchRequest {
  query: string;
  limit?: number | null;
}

export interface DiscoveryResponse {
  query: string;
  category: string;
  location: string;
  requested_limit: number;
  businesses_found: number;
  businesses: LeadCreationOutcome[];
  duration_ms: number;
}

// ---------- Organizations ----------

export interface Organization {
  name: string;
  description: string | null;
  id: number;
  plan_tier: string;
  max_users: number;
  max_leads: number;
  usage_count: number;
  stripe_customer_id: string | null;
  stripe_subscription_id: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string | null;
}

export interface OrganizationCreate {
  name: string;
  description?: string | null;
}

export interface OrganizationUpdate {
  name?: string | null;
  description?: string | null;
  plan_tier?: string | null;
  max_users?: number | null;
  max_leads?: number | null;
}

// ---------- Billing ----------

export interface PlanUsage {
  plan_name: string;
  max_leads_per_day: number;
  can_export: boolean;
  can_use_ai: boolean;
  current_usage: number;
  remaining_daily_leads: number;
  can_process_more_today: boolean;
}

/** The `/plans` response has no fixed schema server-side; treat entries defensively. */
export interface PlanOption {
  name?: string;
  plan_name?: string;
  price?: number | string;
  price_monthly?: number | string;
  max_leads_per_day?: number;
  can_export?: boolean;
  can_use_ai?: boolean;
  features?: string[];
  [key: string]: unknown;
}

// ---------- Analytics ----------

export interface PipelineMetricsSummary {
  total_runs: number;
  success_count: number;
  partial_success_count: number;
  failed_count: number;
  success_rate_pct: number;
  avg_processing_time_ms: number;
  median_processing_time_ms: number;
  p95_processing_time_ms: number;
}

export interface EvaluationMetricsSummary {
  total_evaluations: number;
  average_overall_score: number;
  average_confidence: number;
  average_completeness: number;
  average_grounding: number;
  average_consistency: number;
}

export interface DiscoveryMetricsSummary {
  total_discovery_runs: number;
  total_businesses_found: number;
  total_leads_created: number;
  discovery_success_rate_pct: number;
  website_resolution_rate_pct: number;
  duplicate_removal_rate_pct: number;
  avg_discovery_time_ms: number;
}

// ---------- Errors ----------

export interface ValidationError {
  loc: (string | number)[];
  msg: string;
  type: string;
}

export interface HTTPValidationError {
  detail: ValidationError[];
}

/** Normalized shape the UI works with after unwrapping any axios/HTTP error. */
export interface ApiErrorShape {
  status: number | null;
  message: string;
  fieldErrors?: Record<string, string>;
}

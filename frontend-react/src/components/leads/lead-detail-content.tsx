"use client";

import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  Building2,
  Mail,
  Phone,
  MapPin,
  Linkedin,
  Twitter,
  Facebook,
  ExternalLink,
  RotateCw,
  Trash2,
  Copy,
} from "lucide-react";
import toast from "react-hot-toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { QualificationBadge, StatusBadge } from "@/components/shared/status-badge";
import { ScoreRing } from "@/components/shared/score-ring";
import { ConfirmDialog } from "@/components/shared/confirm-dialog";
import { useLead, useUpdateLead, useDeleteLead, useProcessLead } from "@/features/leads/hooks";
import { leadEditSchema, type LeadEditValues } from "@/lib/validation";
import { ensureProtocol, formatDateTime, formatPercent, getHostname } from "@/lib/utils";
import type { AIExplanation, LeadDetail } from "@/types/api";
import { useState } from "react";

interface LeadDetailContentProps {
  leadId: number;
  onDeleted?: () => void;
}

export function LeadDetailContent({ leadId, onDeleted }: LeadDetailContentProps) {
  const { data: lead, isLoading } = useLead(leadId);
  const updateLead = useUpdateLead(leadId);
  const deleteLead = useDeleteLead();
  const processLead = useProcessLead();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [outreachDraft, setOutreachDraft] = useState("");

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isDirty },
  } = useForm<LeadEditValues>({ resolver: zodResolver(leadEditSchema) });

  useEffect(() => {
    if (lead) {
      reset({
        company_name: lead.company_name,
        industry: lead.industry,
        about_text: lead.about_text,
        contact_name: lead.contact_name,
        contact_title: lead.contact_title,
        email: lead.email,
        phone: lead.phone,
        address: lead.address,
        linkedin_url: lead.linkedin_url,
        twitter_url: lead.twitter_url,
        facebook_url: lead.facebook_url,
      });
      setOutreachDraft(lead.outreach_message ?? "");
    }
  }, [lead, reset]);

  if (isLoading || !lead) {
    return (
      <div className="space-y-4 p-6">
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  const onSave = (values: LeadEditValues) => {
    updateLead.mutate({ ...values, email: values.email || null });
  };

  return (
    <div className="space-y-6 px-1">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-4">
          <ScoreRing value={lead.score} size={64} strokeWidth={6} />
          <div>
            <p className="font-display text-xl font-semibold">{lead.company_name || getHostname(lead.website)}</p>
            <a
              href={ensureProtocol(lead.website)}
              target="_blank"
              rel="noreferrer"
              className="mt-0.5 flex items-center gap-1 text-sm text-muted-foreground hover:text-primary-300"
            >
              {getHostname(lead.website)} <ExternalLink className="h-3 w-3" />
            </a>
            <div className="mt-2">
              <QualificationBadge label={lead.qualification_label} />
            </div>
          </div>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" loading={processLead.isPending} onClick={() => processLead.mutate(lead.id)}>
            <RotateCw className="h-3.5 w-3.5" /> Reprocess
          </Button>
          <Button variant="destructive" size="sm" onClick={() => setConfirmDelete(true)}>
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="contact">Contact</TabsTrigger>
          <TabsTrigger value="enrichment">Enrichment</TabsTrigger>
          <TabsTrigger value="ai-insights">AI insights</TabsTrigger>
          <TabsTrigger value="outreach">Outreach</TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <form onSubmit={handleSubmit(onSave)} className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label>Company name</Label>
                <Input {...register("company_name")} placeholder="—" />
              </div>
              <div className="space-y-1.5">
                <Label>Industry</Label>
                <Input {...register("industry")} placeholder="—" />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label>About</Label>
              <Textarea {...register("about_text")} placeholder="No summary available yet." rows={4} />
            </div>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
                <p className="text-xs text-muted-foreground">Employees</p>
                <p className="mt-1 font-medium">{lead.employees || "—"}</p>
              </div>
              <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
                <p className="text-xs text-muted-foreground">Revenue band</p>
                <p className="mt-1 font-medium">{lead.revenue_band || "—"}</p>
              </div>
            </div>
            {isDirty && (
              <Button type="submit" loading={updateLead.isPending}>
                Save changes
              </Button>
            )}
          </form>
        </TabsContent>

        <TabsContent value="contact">
          <form onSubmit={handleSubmit(onSave)} className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label className="flex items-center gap-1.5"><Building2 className="h-3 w-3" /> Contact name</Label>
                <Input {...register("contact_name")} placeholder="—" />
              </div>
              <div className="space-y-1.5">
                <Label>Title</Label>
                <Input {...register("contact_title")} placeholder="—" />
              </div>
              <div className="space-y-1.5">
                <Label className="flex items-center gap-1.5"><Mail className="h-3 w-3" /> Email</Label>
                <Input type="email" {...register("email")} error={!!errors.email} placeholder="—" />
                {errors.email && <p className="text-xs text-rose-400">{errors.email.message}</p>}
              </div>
              <div className="space-y-1.5">
                <Label className="flex items-center gap-1.5"><Phone className="h-3 w-3" /> Phone</Label>
                <Input {...register("phone")} placeholder="—" />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label className="flex items-center gap-1.5"><MapPin className="h-3 w-3" /> Address</Label>
              <Input {...register("address")} placeholder="—" />
            </div>
            <div className="grid grid-cols-3 gap-4">
              <div className="space-y-1.5">
                <Label className="flex items-center gap-1.5"><Linkedin className="h-3 w-3" /> LinkedIn</Label>
                <Input {...register("linkedin_url")} placeholder="—" />
              </div>
              <div className="space-y-1.5">
                <Label className="flex items-center gap-1.5"><Twitter className="h-3 w-3" /> Twitter</Label>
                <Input {...register("twitter_url")} placeholder="—" />
              </div>
              <div className="space-y-1.5">
                <Label className="flex items-center gap-1.5"><Facebook className="h-3 w-3" /> Facebook</Label>
                <Input {...register("facebook_url")} placeholder="—" />
              </div>
            </div>
            {isDirty && (
              <Button type="submit" loading={updateLead.isPending}>
                Save changes
              </Button>
            )}
          </form>
        </TabsContent>

        <TabsContent value="enrichment">
          <div className="grid grid-cols-3 gap-4">
            <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4 text-center">
              <ScoreRing value={lead.scrape_confidence} size={52} strokeWidth={5} className="mx-auto" />
              <p className="mt-2 text-xs text-muted-foreground">Scrape confidence</p>
              <p className="text-[11px] text-muted-foreground/70">{lead.scrape_source}</p>
            </div>
            <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4 text-center">
              <ScoreRing value={lead.email_confidence} size={52} strokeWidth={5} className="mx-auto" />
              <p className="mt-2 text-xs text-muted-foreground">Email confidence</p>
              <p className="text-[11px] text-muted-foreground/70">{lead.email_source}</p>
            </div>
            <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4 text-center">
              <ScoreRing value={lead.enrichment_confidence} size={52} strokeWidth={5} className="mx-auto" />
              <p className="mt-2 text-xs text-muted-foreground">Enrichment confidence</p>
              <p className="text-[11px] text-muted-foreground/70">{lead.enrichment_source}</p>
            </div>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-4 text-sm">
            <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
              <p className="text-xs text-muted-foreground">Overall score</p>
              <p className="mt-1 font-mono font-medium">{formatPercent(lead.score, lead.score > 1)}</p>
            </div>
            <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
              <p className="text-xs text-muted-foreground">Founded</p>
              <p className="mt-1 font-medium">{lead.founded_year || "—"}</p>
            </div>
          </div>
          <div className="mt-4 space-y-2 rounded-xl border border-white/10 bg-white/[0.03] p-4 text-xs text-muted-foreground">
            <p>Created {formatDateTime(lead.created_at)}</p>
            <p>Last updated {formatDateTime(lead.updated_at)}</p>
            <p>Status: {lead.is_active ? "Active" : "Inactive"} · {lead.is_verified ? "Verified" : "Unverified"}</p>
          </div>
        </TabsContent>

        <TabsContent value="ai-insights">
          <AIInsightsTab lead={lead} />
        </TabsContent>

        <TabsContent value="outreach">
          {lead.outreach_message ? (
            <div className="space-y-3">
              <Textarea value={outreachDraft} onChange={(e) => setOutreachDraft(e.target.value)} rows={8} />
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    navigator.clipboard.writeText(outreachDraft);
                    toast.success("Draft copied to clipboard.");
                  }}
                >
                  <Copy className="h-3.5 w-3.5" /> Copy
                </Button>
                {outreachDraft !== lead.outreach_message && (
                  <Button size="sm" loading={updateLead.isPending} onClick={() => updateLead.mutate({ outreach_message: outreachDraft })}>
                    Save edits
                  </Button>
                )}
                {lead.email && (
                  <Button variant="secondary" size="sm" asChild>
                    <a href={`mailto:${lead.email}?body=${encodeURIComponent(outreachDraft)}`}>
                      <Mail className="h-3.5 w-3.5" /> Open in email
                    </a>
                  </Button>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                {lead.outreach_sent ? `Sent ${formatDateTime(lead.outreach_sent_at)}` : "Not marked as sent yet."}
              </p>
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-white/10 bg-white/[0.02] p-6 text-center text-sm text-muted-foreground">
              No outreach draft has been generated for this lead yet.
            </div>
          )}
        </TabsContent>
      </Tabs>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete this lead?"
        description={`This will remove "${lead.company_name || lead.website}" from your leads. This can't be undone.`}
        confirmLabel="Delete lead"
        destructive
        loading={deleteLead.isPending}
        onConfirm={() => {
          deleteLead.mutate(lead.id);
          setConfirmDelete(false);
          onDeleted?.();
        }}
      />
    </div>
  );
}

/**
 * Renders the AI insights bundle returned by GET /leads/{id}
 * (`lead.ai_insights`): Company Intelligence, Decision, Evaluation, Review,
 * and Messaging output produced by the pipeline. Each stage is `null` until
 * the pipeline has actually run it for this lead (e.g. `messaging` stays
 * null when the lead was routed to human review), which this renders as an
 * explicit "not processed yet" state rather than omitting the section.
 */
function AIInsightsTab({ lead }: { lead: LeadDetail }) {
  const insights = lead.ai_insights;
  const { company_intelligence, decision, evaluation, review, messaging } = insights;

  return (
    <div className="space-y-4">
      <InsightSection title="Company intelligence" empty={!company_intelligence} emptyText="Not analyzed yet.">
        {company_intelligence && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <InsightField label="Industry analysis" value={company_intelligence.industry_analysis} />
              <InsightField label="Website quality" value={company_intelligence.website_quality} />
              <InsightField label="Market position" value={company_intelligence.market_position} />
              <div>
                <p className="text-xs text-muted-foreground">ICP alignment</p>
                <p className="mt-1 font-mono font-medium">{formatPercent(company_intelligence.icp_alignment_score)}</p>
              </div>
            </div>
            <TagList label="Technology signals" items={company_intelligence.technology_signals} />
            <TagList label="Pain points" items={company_intelligence.pain_points} />
            <TagList label="Growth indicators" items={company_intelligence.growth_indicators} />
            <ExplanationBlock explanation={company_intelligence.explanation} source={company_intelligence.source} />
          </div>
        )}
      </InsightSection>

      <InsightSection title="Decision" empty={!decision} emptyText="Not evaluated yet.">
        {decision && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <QualificationBadge label={decision.qualification} />
              <StatusBadge status={decision.recommended_action} />
            </div>
            <ExplanationBlock explanation={decision.explanation} source={decision.source} />
          </div>
        )}
      </InsightSection>

      <InsightSection title="Evaluation" empty={!evaluation} emptyText="Not evaluated yet.">
        {evaluation && (
          <div className="space-y-3">
            <div className="grid grid-cols-4 gap-3 text-center text-sm">
              <MetricPill label="Confidence" value={evaluation.confidence} />
              <MetricPill label="Completeness" value={evaluation.completeness} />
              <MetricPill label="Grounding" value={evaluation.grounding} />
              <MetricPill label="Consistency" value={evaluation.consistency} />
            </div>
            <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3 text-center">
              <p className="text-xs text-muted-foreground">Overall</p>
              <p className="mt-1 font-mono font-medium">{formatPercent(evaluation.overall)}</p>
            </div>
            {evaluation.notes.length > 0 && (
              <ul className="list-disc space-y-1 pl-4 text-sm text-muted-foreground">
                {evaluation.notes.map((note, i) => (
                  <li key={i}>{note}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </InsightSection>

      <InsightSection title="Review routing" empty={!review} emptyText="Not routed yet.">
        {review && (
          <div className="space-y-2 text-sm">
            <StatusBadge status={review.decision} />
            {review.reason && <p className="text-muted-foreground">{review.reason}</p>}
            {review.threshold_used !== null && (
              <p className="text-xs text-muted-foreground/70">Threshold used: {formatPercent(review.threshold_used)}</p>
            )}
          </div>
        )}
      </InsightSection>

      <InsightSection
        title="Messaging"
        empty={!messaging}
        emptyText="No message generated yet — this lead may be pending human review."
      >
        {messaging && (
          <div className="space-y-3 text-sm">
            {messaging.email_subject && (
              <InsightField label="Email subject" value={messaging.email_subject} />
            )}
            {messaging.email_body && <InsightField label="Email body" value={messaging.email_body} multiline />}
            {messaging.linkedin_opener && (
              <InsightField label="LinkedIn opener" value={messaging.linkedin_opener} multiline />
            )}
            {messaging.follow_up_strategy && (
              <InsightField label="Follow-up strategy" value={messaging.follow_up_strategy} multiline />
            )}
            {messaging.channel_notes && <InsightField label="Channel notes" value={messaging.channel_notes} multiline />}
            <ExplanationBlock explanation={messaging.explanation} source={messaging.source} />
          </div>
        )}
      </InsightSection>
    </div>
  );
}

function InsightSection({
  title,
  empty,
  emptyText,
  children,
}: {
  title: string;
  empty: boolean;
  emptyText: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
      <p className="mb-3 text-sm font-medium">{title}</p>
      {empty ? <p className="text-sm text-muted-foreground">{emptyText}</p> : children}
    </div>
  );
}

function InsightField({ label, value, multiline }: { label: string; value: string | null; multiline?: boolean }) {
  if (!value) return null;
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={multiline ? "mt-1 whitespace-pre-wrap text-sm" : "mt-1 text-sm font-medium"}>{value}</p>
    </div>
  );
}

function TagList({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <div className="mt-1 flex flex-wrap gap-1.5">
        {items.map((item, i) => (
          <span key={i} className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-xs">
            {item}
          </span>
        ))}
      </div>
    </div>
  );
}

function MetricPill({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-2">
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className="mt-0.5 font-mono text-sm font-medium">{formatPercent(value)}</p>
    </div>
  );
}

function ExplanationBlock({ explanation, source }: { explanation: AIExplanation; source: string }) {
  if (!explanation.reasoning && explanation.evidence.length === 0) return null;
  return (
    <div className="space-y-1.5 border-t border-white/10 pt-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">Reasoning</p>
        <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[11px] text-muted-foreground">
          {source} · {formatPercent(explanation.confidence)} confidence
        </span>
      </div>
      {explanation.reasoning && <p className="text-sm">{explanation.reasoning}</p>}
      {explanation.evidence.length > 0 && (
        <ul className="list-disc space-y-1 pl-4 text-sm text-muted-foreground">
          {explanation.evidence.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

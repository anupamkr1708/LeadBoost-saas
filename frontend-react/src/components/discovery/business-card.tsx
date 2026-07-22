"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { ExternalLink, RotateCw, Eye, AlertCircle } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/shared/status-badge";
import { useProcessLead } from "@/features/leads/hooks";
import { ensureProtocol, getHostname } from "@/lib/utils";
import type { LeadCreationOutcome } from "@/types/api";

interface BusinessCardProps {
  outcome: LeadCreationOutcome;
  index: number;
}

/** One discovered business, with its creation + pipeline outcome and available actions. */
export function BusinessCard({ outcome, index }: BusinessCardProps) {
  const processLead = useProcessLead();
  const failed = outcome.pipeline_status === "FAILED" || outcome.status.toUpperCase().includes("FAIL");

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: Math.min(index * 0.05, 0.6) }}
    >
      <Card className="p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <p className="truncate font-display text-sm font-semibold">{outcome.name}</p>
            {outcome.website ? (
              <a
                href={ensureProtocol(outcome.website)}
                target="_blank"
                rel="noreferrer"
                className="mt-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-primary-300"
              >
                {getHostname(outcome.website)} <ExternalLink className="h-3 w-3" />
              </a>
            ) : (
              <p className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
                <AlertCircle className="h-3 w-3" /> {outcome.reason ?? "No website resolved"}
              </p>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={outcome.pipeline_status ?? outcome.status} />

            {outcome.website && (
              <Button variant="secondary" size="sm" asChild>
                <a href={ensureProtocol(outcome.website)} target="_blank" rel="noreferrer">
                  <ExternalLink className="h-3.5 w-3.5" /> Site
                </a>
              </Button>
            )}

            {outcome.lead_id && (
              <Button variant="secondary" size="sm" asChild>
                <Link href={`/leads/${outcome.lead_id}`}>
                  <Eye className="h-3.5 w-3.5" /> View
                </Link>
              </Button>
            )}

            {outcome.lead_id && failed && (
              <Button
                variant="secondary"
                size="sm"
                loading={processLead.isPending}
                onClick={() => processLead.mutate(outcome.lead_id as number)}
              >
                <RotateCw className="h-3.5 w-3.5" /> Retry
              </Button>
            )}
          </div>
        </div>

        {outcome.reason && outcome.website && (
          <p className="mt-3 rounded-lg bg-white/[0.03] px-3 py-2 text-xs text-muted-foreground">{outcome.reason}</p>
        )}
      </Card>
    </motion.div>
  );
}

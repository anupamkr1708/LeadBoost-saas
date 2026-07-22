"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { ExternalLink } from "lucide-react";
import type { Lead } from "@/types/api";
import { QualificationBadge } from "@/components/shared/status-badge";
import { EmptyState } from "@/components/shared/empty-state";
import { formatDateTime, getHostname } from "@/lib/utils";
import { Users } from "lucide-react";

interface ActivityFeedProps {
  leads: Lead[];
}

/** A compact feed of the most recently created leads — the dashboard's "Latest Discoveries". */
export function ActivityFeed({ leads }: ActivityFeedProps) {
  if (leads.length === 0) {
    return <EmptyState icon={Users} title="No leads yet" description="Run a discovery search or add a lead to see activity here." />;
  }

  return (
    <div className="divide-y divide-white/[0.06]">
      {leads.map((lead, i) => (
        <motion.div
          key={lead.id}
          initial={{ opacity: 0, x: -8 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.35, delay: i * 0.04 }}
        >
          <Link href={`/leads/${lead.id}`} className="flex items-center justify-between gap-4 px-1 py-3.5 transition-colors hover:bg-white/[0.03]">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{lead.company_name || getHostname(lead.website)}</p>
              <p className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
                {getHostname(lead.website)} <ExternalLink className="h-3 w-3" />
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-3">
              <QualificationBadge label={lead.qualification_label} />
              <span className="hidden text-xs text-muted-foreground sm:inline">{formatDateTime(lead.created_at)}</span>
            </div>
          </Link>
        </motion.div>
      ))}
    </div>
  );
}

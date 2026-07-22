"use client";

import { useRef, useState } from "react";
import { motion } from "framer-motion";
import { Sparkles, Loader2, X, Info } from "lucide-react";
import { SearchBox } from "@/components/discovery/search-box";
import { BusinessCard } from "@/components/discovery/business-card";
import { EmptyState } from "@/components/shared/empty-state";
import { ErrorState } from "@/components/shared/error-state";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDiscoverySearch } from "@/features/discovery/hooks";
import { useElapsedSeconds } from "@/hooks/use-elapsed-seconds";
import { normalizeApiError } from "@/lib/api-client";
import { formatDuration } from "@/lib/utils";
import type { DiscoverySearchValues } from "@/lib/validation";

export default function DiscoveryPage() {
  const search = useDiscoverySearch();
  const [lastQuery, setLastQuery] = useState<string | null>(null);
  const [canceled, setCanceled] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);
  const elapsed = useElapsedSeconds(search.isPending);

  const handleSearch = (values: DiscoverySearchValues) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setCanceled(false);
    setLastQuery(values.query);
    search.mutate({ query: values.query, limit: values.limit ?? 20, signal: controller.signal });
  };

  const handleCancel = () => {
    controllerRef.current?.abort();
    setCanceled(true);
  };

  const mm = String(Math.floor(elapsed / 60)).padStart(2, "0");
  const ss = String(elapsed % 60).padStart(2, "0");

  return (
    <div className="space-y-8">
      <div className="space-y-1">
        <h1 className="font-display text-2xl font-semibold tracking-tight sm:text-3xl">Lead Discovery</h1>
        <p className="text-sm text-muted-foreground">
          Describe who you&apos;re looking for in plain English — LeadBoost finds, verifies, and enriches matching businesses.
        </p>
      </div>

      <SearchBox onSearch={handleSearch} loading={search.isPending} />

      {search.isPending && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-primary-500/20 bg-primary-500/[0.05] px-4 py-3">
            <div className="flex items-center gap-2 text-sm">
              <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary-400" />
              <span>
                Running discovery for &ldquo;{lastQuery}&rdquo;… resolving websites, enriching, and scoring each result.
                {elapsed >= 20 && " Larger searches can take a few minutes — this is normal."}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <span className="font-mono text-xs text-muted-foreground">
                {mm}:{ss}
              </span>
              <Button variant="secondary" size="sm" onClick={handleCancel}>
                <X className="h-3.5 w-3.5" /> Cancel
              </Button>
            </div>
          </div>
          {Array.from({ length: 4 }).map((_, i) => (
            <Card key={i} className="p-5">
              <div className="flex items-center justify-between">
                <div className="space-y-2">
                  <Skeleton className="h-4 w-48" />
                  <Skeleton className="h-3 w-32" />
                </div>
                <Skeleton className="h-6 w-20 rounded-full" />
              </div>
            </Card>
          ))}
        </div>
      )}

      {search.isError && canceled && (
        <div className="flex items-start gap-3 rounded-2xl border border-white/10 bg-white/[0.03] p-5 text-sm text-muted-foreground">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-primary-400" />
          <p>
            Search canceled. This only stopped the frontend from waiting — the backend runs discovery synchronously,
            so if it had already started processing, it may still finish and create leads in the background. Check
            the <a href="/leads" className="text-primary-300 hover:underline">Leads page</a> in a bit if you&apos;re unsure.
          </p>
        </div>
      )}

      {search.isError && !canceled && (
        <ErrorState message={normalizeApiError(search.error).message} onRetry={() => search.reset()} />
      )}

      {search.isSuccess && search.data && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
            <span>
              <span className="font-medium text-foreground">{search.data.businesses_found}</span> businesses found for &ldquo;
              {search.data.query}&rdquo; in {search.data.location}
            </span>
            <span className="font-mono text-xs">{formatDuration(search.data.duration_ms)}</span>
          </div>

          {search.data.businesses.length === 0 ? (
            <EmptyState
              icon={Sparkles}
              title="No matching businesses found"
              description="Try a broader description or a different location."
            />
          ) : (
            <div className="space-y-3">
              {search.data.businesses.map((outcome, i) => (
                <BusinessCard key={`${outcome.name}-${i}`} outcome={outcome} index={i} />
              ))}
            </div>
          )}
        </motion.div>
      )}

      {!search.isPending && !search.isSuccess && !search.isError && (
        <EmptyState
          icon={Sparkles}
          title="Search for your next customers"
          description="Try one of the example searches above, or describe your ideal lead in your own words."
        />
      )}
    </div>
  );
}

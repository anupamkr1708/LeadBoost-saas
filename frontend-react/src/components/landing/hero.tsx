"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { ArrowRight, Sparkles, TrendingUp, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";

const MOCK_RESULTS = [
  { name: "Bloom & Sole Footwear", score: 92, status: "Qualified" },
  { name: "Marina Kicks Co.", score: 78, status: "Qualified" },
  { name: "Urban Step Studio", score: 54, status: "Warm" },
];

export function Hero() {
  return (
    <section className="relative overflow-hidden pb-20 pt-20 sm:pt-28">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="grid items-center gap-16 lg:grid-cols-2">
          <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}>
            <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-3.5 py-1.5 text-xs font-medium text-muted-foreground">
              <Sparkles className="h-3.5 w-3.5 text-primary-400" />
              AI-native lead intelligence
            </div>
            <h1 className="font-display text-4xl font-semibold leading-[1.1] tracking-tight sm:text-5xl lg:text-6xl">
              Find and qualify <span className="text-gradient">high-value leads</span> with AI.
            </h1>
            <p className="mt-6 max-w-lg text-lg text-muted-foreground">
              Describe who you&apos;re looking for in plain English. LeadBoost discovers real businesses, verifies their
              websites, enriches every profile, and scores them — automatically.
            </p>
            <div className="mt-8 flex flex-col gap-3 sm:flex-row">
              <Button size="lg" asChild>
                <Link href="/register">
                  Start finding leads <ArrowRight className="h-4 w-4" />
                </Link>
              </Button>
              <Button size="lg" variant="secondary" asChild>
                <a href="#discovery">See it in action</a>
              </Button>
            </div>
            <div className="mt-10 flex items-center gap-6 text-xs text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" /> No credit card required
              </span>
              <span className="flex items-center gap-1.5">
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" /> Setup in minutes
              </span>
            </div>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 24, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 0.7, delay: 0.15, ease: [0.16, 1, 0.3, 1] }}
            className="relative"
          >
            <div className="glass-strong overflow-hidden p-1.5 shadow-elevated">
              <div className="rounded-xl bg-canvas-charcoal/60 p-5">
                <div className="mb-4 flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.04] px-4 py-3">
                  <Sparkles className="h-4 w-4 text-primary-400" />
                  <span className="text-sm text-foreground">Top shoe stores in Mumbai</span>
                  <span className="ml-auto h-1.5 w-1.5 animate-pulse-glow rounded-full bg-primary-400" />
                </div>
                <div className="space-y-2.5">
                  {MOCK_RESULTS.map((r, i) => (
                    <motion.div
                      key={r.name}
                      initial={{ opacity: 0, x: 12 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ duration: 0.5, delay: 0.5 + i * 0.18 }}
                      className="flex items-center justify-between rounded-xl border border-white/[0.06] bg-white/[0.03] px-4 py-3"
                    >
                      <div>
                        <p className="text-sm font-medium">{r.name}</p>
                        <p className="text-xs text-muted-foreground">{r.status}</p>
                      </div>
                      <div className="flex items-center gap-1.5 rounded-full bg-emerald-500/15 px-2.5 py-1 text-xs font-semibold text-emerald-300">
                        <TrendingUp className="h-3 w-3" /> {r.score}
                      </div>
                    </motion.div>
                  ))}
                </div>
              </div>
            </div>
            <div className="absolute -right-6 -top-6 h-24 w-24 rounded-full bg-primary-500/30 blur-3xl" />
            <div className="absolute -bottom-8 -left-8 h-32 w-32 rounded-full bg-fuchsia-500/20 blur-3xl" />
          </motion.div>
        </div>
      </div>
    </section>
  );
}

"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const TIERS = [
  {
    name: "Starter",
    price: "$0",
    cadence: "/mo",
    description: "Try discovery and scoring on a small volume of leads.",
    features: ["50 leads / day", "Manual lead creation", "Basic qualification scoring", "Community support"],
    cta: "Start free",
    highlighted: false,
  },
  {
    name: "Growth",
    price: "$79",
    cadence: "/mo",
    description: "For teams running discovery and outreach every week.",
    features: ["500 leads / day", "AI-powered discovery search", "AI enrichment & scoring", "Outreach drafting", "CSV export"],
    cta: "Start free trial",
    highlighted: true,
  },
  {
    name: "Scale",
    price: "Custom",
    cadence: "",
    description: "For organizations with high-volume pipelines and SLAs.",
    features: ["Unlimited leads", "Priority processing", "Dedicated support", "Custom quotas & seats"],
    cta: "Talk to sales",
    highlighted: false,
  },
];

export function Pricing() {
  return (
    <section id="pricing" className="relative py-24">
      <div className="mx-auto max-w-6xl px-4 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-2xl text-center">
          <p className="text-xs font-medium uppercase tracking-widest text-primary-400">Pricing</p>
          <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight sm:text-4xl">Simple plans that scale with your pipeline</h2>
          <p className="mt-4 text-muted-foreground">Exact quotas and usage are always visible in your billing dashboard once you sign in.</p>
        </div>

        <div className="mt-14 grid gap-6 lg:grid-cols-3">
          {TIERS.map((tier, i) => (
            <motion.div
              key={tier.name}
              initial={{ opacity: 0, y: 16 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, delay: i * 0.08 }}
              className={cn(
                "relative flex flex-col rounded-3xl border p-8",
                tier.highlighted
                  ? "border-primary-500/40 bg-gradient-to-b from-primary-500/[0.08] to-transparent shadow-glow"
                  : "glass"
              )}
            >
              {tier.highlighted && (
                <span className="absolute -top-3 left-8 rounded-full bg-gradient-to-r from-primary-400 to-fuchsia-500 px-3 py-1 text-xs font-semibold text-white">
                  Most popular
                </span>
              )}
              <h3 className="font-display text-lg font-semibold">{tier.name}</h3>
              <div className="mt-3 flex items-baseline gap-1">
                <span className="font-display text-4xl font-semibold tracking-tight">{tier.price}</span>
                <span className="text-sm text-muted-foreground">{tier.cadence}</span>
              </div>
              <p className="mt-3 text-sm text-muted-foreground">{tier.description}</p>
              <ul className="mt-6 flex-1 space-y-3">
                {tier.features.map((f) => (
                  <li key={f} className="flex items-start gap-2.5 text-sm">
                    <Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
                    {f}
                  </li>
                ))}
              </ul>
              <Button className="mt-8 w-full" variant={tier.highlighted ? "default" : "secondary"} asChild>
                <Link href="/register">{tier.cta}</Link>
              </Button>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}

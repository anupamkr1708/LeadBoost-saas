"use client";

import { motion } from "framer-motion";
import { Sparkles, ShieldCheck, Gauge, Mail, GitBranch, BarChart3 } from "lucide-react";

const FEATURES = [
  {
    icon: Sparkles,
    title: "Natural-language discovery",
    description: "Describe your ideal customer in plain English — LeadBoost finds real, verified businesses that match.",
  },
  {
    icon: ShieldCheck,
    title: "Website verification",
    description: "Every discovered business is resolved to a live, reachable website before it becomes a lead.",
  },
  {
    icon: Gauge,
    title: "AI qualification scoring",
    description: "Leads are automatically scored and labeled, so your team focuses on the ones worth chasing.",
  },
  {
    icon: GitBranch,
    title: "Transparent pipeline",
    description: "Track every lead through discovery, enrichment, and scoring with clear success and failure states.",
  },
  {
    icon: Mail,
    title: "AI-drafted outreach",
    description: "Get a tailored outreach message drafted for every qualified lead, ready to review and send.",
  },
  {
    icon: BarChart3,
    title: "Real performance analytics",
    description: "See discovery success rate, qualification distribution, and processing performance at a glance.",
  },
];

export function Features() {
  return (
    <section id="features" className="relative py-24">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-2xl text-center">
          <p className="text-xs font-medium uppercase tracking-widest text-primary-400">Built for revenue teams</p>
          <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight sm:text-4xl">
            Everything between a search bar and a booked meeting
          </h2>
        </div>

        <div className="mt-14 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((f, i) => (
            <motion.div
              key={f.title}
              initial={{ opacity: 0, y: 16 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-60px" }}
              transition={{ duration: 0.5, delay: i * 0.06 }}
              className="glass glass-hover p-6"
            >
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary-500/25 to-fuchsia-500/10 text-primary-300">
                <f.icon className="h-5 w-5" />
              </div>
              <h3 className="font-display text-base font-semibold">{f.title}</h3>
              <p className="mt-2 text-sm text-muted-foreground">{f.description}</p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}

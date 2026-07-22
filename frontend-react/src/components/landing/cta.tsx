"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";

export function Cta() {
  return (
    <section className="relative py-24">
      <div className="mx-auto max-w-5xl px-4 sm:px-6 lg:px-8">
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
          className="relative overflow-hidden rounded-3xl border border-white/10 bg-gradient-to-br from-primary-500/15 via-fuchsia-500/10 to-transparent px-8 py-16 text-center sm:px-16"
        >
          <div className="absolute -left-10 -top-10 h-56 w-56 rounded-full bg-primary-500/20 blur-3xl" />
          <div className="absolute -bottom-10 -right-10 h-56 w-56 rounded-full bg-fuchsia-500/20 blur-3xl" />
          <h2 className="relative font-display text-3xl font-semibold tracking-tight sm:text-4xl">
            Your next best customer is one search away.
          </h2>
          <p className="relative mx-auto mt-4 max-w-xl text-muted-foreground">
            Start discovering, qualifying, and reaching qualified leads today — no credit card required.
          </p>
          <div className="relative mt-8 flex justify-center">
            <Button size="lg" asChild>
              <Link href="/register">
                Get started for free <ArrowRight className="h-4 w-4" />
              </Link>
            </Button>
          </div>
        </motion.div>
      </div>
    </section>
  );
}

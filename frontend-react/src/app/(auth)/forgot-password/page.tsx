"use client";

import { useState } from "react";
import Link from "next/link";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Mail, ArrowLeft, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { forgotPasswordSchema, type ForgotPasswordValues } from "@/lib/validation";

/**
 * Password-reset request flow. The API doesn't yet expose a reset-password
 * endpoint, so this collects the request client-side and confirms receipt —
 * wire this up to a backend endpoint once one exists.
 */
export default function ForgotPasswordPage() {
  const [sent, setSent] = useState(false);
  const {
    register,
    handleSubmit,
    formState: { errors },
    getValues,
  } = useForm<ForgotPasswordValues>({ resolver: zodResolver(forgotPasswordSchema) });

  if (sent) {
    return (
      <Card className="animate-fade-up">
        <CardContent className="flex flex-col items-center gap-3 pt-10 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-400">
            <CheckCircle2 className="h-6 w-6" />
          </div>
          <p className="font-display text-lg font-semibold">Check your inbox</p>
          <p className="max-w-xs text-sm text-muted-foreground">
            If an account exists for {getValues("email")}, we&apos;ve sent instructions to reset the password.
          </p>
          <Link href="/login" className="mt-2 inline-flex items-center gap-1.5 text-sm font-medium text-primary-400 hover:underline">
            <ArrowLeft className="h-3.5 w-3.5" /> Back to sign in
          </Link>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="animate-fade-up">
      <CardHeader>
        <CardTitle className="font-display text-2xl">Reset your password</CardTitle>
        <CardDescription>We&apos;ll email you a link to get back into your account.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit(() => setSent(true))} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="email">Email</Label>
            <div className="relative">
              <Mail className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input id="email" type="email" placeholder="you@company.com" className="pl-10" error={!!errors.email} {...register("email")} />
            </div>
            {errors.email && <p className="text-xs text-rose-400">{errors.email.message}</p>}
          </div>
          <Button type="submit" size="lg" className="w-full">
            Send reset link
          </Button>
        </form>
        <Link href="/login" className="mt-6 flex items-center justify-center gap-1.5 text-sm font-medium text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-3.5 w-3.5" /> Back to sign in
        </Link>
      </CardContent>
    </Card>
  );
}

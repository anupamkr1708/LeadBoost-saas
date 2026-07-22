"use client";

import Link from "next/link";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Mail, Lock, User, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { useRegister } from "@/features/auth/hooks";
import { registerSchema, type RegisterValues } from "@/lib/validation";

export default function RegisterPage() {
  const registerMutation = useRegister();
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<RegisterValues>({ resolver: zodResolver(registerSchema) });

  const onSubmit = (values: RegisterValues) =>
    registerMutation.mutate({
      email: values.email,
      password: values.password,
      first_name: values.firstName,
      last_name: values.lastName,
    });

  return (
    <Card className="animate-fade-up">
      <CardHeader>
        <CardTitle className="font-display text-2xl">Create your account</CardTitle>
        <CardDescription>Start discovering and qualifying leads with AI in minutes.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="firstName">First name</Label>
              <div className="relative">
                <User className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input id="firstName" placeholder="Ada" className="pl-10" error={!!errors.firstName} {...register("firstName")} />
              </div>
              {errors.firstName && <p className="text-xs text-rose-400">{errors.firstName.message}</p>}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="lastName">Last name</Label>
              <Input id="lastName" placeholder="Lovelace" error={!!errors.lastName} {...register("lastName")} />
              {errors.lastName && <p className="text-xs text-rose-400">{errors.lastName.message}</p>}
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="email">Work email</Label>
            <div className="relative">
              <Mail className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input id="email" type="email" placeholder="you@company.com" className="pl-10" error={!!errors.email} {...register("email")} />
            </div>
            {errors.email && <p className="text-xs text-rose-400">{errors.email.message}</p>}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="password">Password</Label>
            <div className="relative">
              <Lock className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input id="password" type="password" placeholder="At least 8 characters" className="pl-10" error={!!errors.password} {...register("password")} />
            </div>
            {errors.password && <p className="text-xs text-rose-400">{errors.password.message}</p>}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="confirmPassword">Confirm password</Label>
            <div className="relative">
              <Lock className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input id="confirmPassword" type="password" placeholder="Repeat your password" className="pl-10" error={!!errors.confirmPassword} {...register("confirmPassword")} />
            </div>
            {errors.confirmPassword && <p className="text-xs text-rose-400">{errors.confirmPassword.message}</p>}
          </div>

          <Button type="submit" size="lg" className="w-full" loading={registerMutation.isPending}>
            Create account <ArrowRight className="h-4 w-4" />
          </Button>
        </form>

        <p className="mt-6 text-center text-sm text-muted-foreground">
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-primary-400 hover:underline">
            Sign in
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}

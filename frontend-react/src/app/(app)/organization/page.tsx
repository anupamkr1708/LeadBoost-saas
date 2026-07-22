"use client";

import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Building2, Users, Gauge } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { useOrganization, useUpdateOrganization } from "@/features/organizations/hooks";
import { orgEditSchema, type OrgEditValues } from "@/lib/validation";
import { formatDate } from "@/lib/utils";

export default function OrganizationPage() {
  const { data: org, isLoading } = useOrganization();
  const updateOrg = useUpdateOrganization(org?.id ?? 0);

  const {
    register,
    handleSubmit,
    reset,
    formState: { isDirty, errors },
  } = useForm<OrgEditValues>({ resolver: zodResolver(orgEditSchema) });

  useEffect(() => {
    if (org) reset({ name: org.name, description: org.description });
  }, [org, reset]);

  return (
    <div className="space-y-8">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary-500/15 text-primary-300">
          <Building2 className="h-5 w-5" />
        </div>
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight">Organization</h1>
          <p className="text-sm text-muted-foreground">Manage your organization&apos;s profile and limits.</p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Profile</CardTitle>
          <CardDescription>Visible to everyone on your team.</CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          {isLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-20 w-full" />
            </div>
          ) : (
            <form onSubmit={handleSubmit((values) => updateOrg.mutate(values))} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="name">Organization name</Label>
                <Input id="name" {...register("name")} error={!!errors.name} />
                {errors.name && <p className="text-xs text-rose-400">{errors.name.message}</p>}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="description">Description</Label>
                <Textarea id="description" rows={3} {...register("description")} placeholder="What does your team do?" />
              </div>
              {isDirty && (
                <Button type="submit" loading={updateOrg.isPending}>
                  Save changes
                </Button>
              )}
            </form>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Gauge className="h-4 w-4 text-muted-foreground" /> Plan & limits
          </CardTitle>
          <CardDescription>Read-only — manage your subscription from Billing.</CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          {isLoading ? (
            <Skeleton className="h-20 w-full" />
          ) : org ? (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
                <p className="text-xs text-muted-foreground">Plan tier</p>
                <Badge variant="primary" className="mt-1.5">{org.plan_tier}</Badge>
              </div>
              <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
                <p className="text-xs text-muted-foreground">Max users</p>
                <p className="mt-1 font-medium">{org.max_users}</p>
              </div>
              <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
                <p className="text-xs text-muted-foreground">Max leads</p>
                <p className="mt-1 font-medium">{org.max_leads}</p>
              </div>
              <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
                <p className="text-xs text-muted-foreground">Member since</p>
                <p className="mt-1 font-medium">{formatDate(org.created_at)}</p>
              </div>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Users className="h-4 w-4 text-muted-foreground" /> Team members
          </CardTitle>
          <CardDescription>Member management is coming soon.</CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          <div className="rounded-xl border border-dashed border-white/10 bg-white/[0.02] p-6 text-center text-sm text-muted-foreground">
            Inviting and managing teammates isn&apos;t available yet — check back soon.
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

"use client";

import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { UserCircle, Mail, ShieldCheck, Building2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { useCurrentUser, useUpdateProfile } from "@/features/auth/hooks";
import { profileEditSchema, type ProfileEditValues } from "@/lib/validation";
import { getInitials, formatDate } from "@/lib/utils";

export default function ProfilePage() {
  const { data: user, isLoading } = useCurrentUser();
  const updateProfile = useUpdateProfile();

  const {
    register,
    handleSubmit,
    reset,
    formState: { isDirty },
  } = useForm<ProfileEditValues>({ resolver: zodResolver(profileEditSchema) });

  useEffect(() => {
    if (user) reset({ first_name: user.first_name, last_name: user.last_name });
  }, [user, reset]);

  return (
    <div className="space-y-8">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary-500/15 text-primary-300">
          <UserCircle className="h-5 w-5" />
        </div>
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight">Profile</h1>
          <p className="text-sm text-muted-foreground">Your personal account details.</p>
        </div>
      </div>

      <Tabs defaultValue="personal">
        <TabsList>
          <TabsTrigger value="personal">Personal</TabsTrigger>
          <TabsTrigger value="security">Security</TabsTrigger>
        </TabsList>

        <TabsContent value="personal">
          <Card>
            <CardHeader className="flex flex-row items-center gap-4">
              <Avatar className="h-14 w-14">
                <AvatarFallback className="text-base">{getInitials(`${user?.first_name ?? ""} ${user?.last_name ?? ""}`, user?.email)}</AvatarFallback>
              </Avatar>
              <div>
                <CardTitle>{user ? `${user.first_name ?? ""} ${user.last_name ?? ""}`.trim() || user.email : "—"}</CardTitle>
                <CardDescription className="flex items-center gap-1.5">
                  <Mail className="h-3 w-3" /> {user?.email}
                </CardDescription>
              </div>
            </CardHeader>
            <CardContent className="pt-0">
              {isLoading ? (
                <Skeleton className="h-24 w-full" />
              ) : (
                <form onSubmit={handleSubmit((values) => updateProfile.mutate(values))} className="space-y-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-1.5">
                      <Label htmlFor="first_name">First name</Label>
                      <Input id="first_name" {...register("first_name")} />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="last_name">Last name</Label>
                      <Input id="last_name" {...register("last_name")} />
                    </div>
                  </div>
                  <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3 text-xs text-muted-foreground">
                    Member since {formatDate(user?.created_at)} · Account {user?.is_verified ? "verified" : "unverified"}
                  </div>
                  {isDirty && (
                    <Button type="submit" loading={updateProfile.isPending}>
                      Save changes
                    </Button>
                  )}
                </form>
              )}
            </CardContent>
          </Card>

          <Card className="mt-6">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Building2 className="h-4 w-4 text-muted-foreground" /> Organization
              </CardTitle>
              <CardDescription>Manage shared settings on the Organization page.</CardDescription>
            </CardHeader>
            <CardContent className="pt-0">
              <Button variant="secondary" size="sm" asChild>
                <a href="/organization">View organization</a>
              </Button>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="security">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <ShieldCheck className="h-4 w-4 text-muted-foreground" /> Password & security
              </CardTitle>
              <CardDescription>Self-serve password changes aren&apos;t available yet.</CardDescription>
            </CardHeader>
            <CardContent className="pt-0">
              <div className="rounded-xl border border-dashed border-white/10 bg-white/[0.02] p-6 text-center text-sm text-muted-foreground">
                Contact support to reset your password or review account security.
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

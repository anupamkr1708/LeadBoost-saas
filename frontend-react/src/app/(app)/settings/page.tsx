"use client";

import { Settings as SettingsIcon, Moon, Bell, LogOut } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { useLogout } from "@/features/auth/hooks";

export default function SettingsPage() {
  const logout = useLogout();

  return (
    <div className="space-y-8">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary-500/15 text-primary-300">
          <SettingsIcon className="h-5 w-5" />
        </div>
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight">Settings</h1>
          <p className="text-sm text-muted-foreground">Preferences for how LeadBoost looks and notifies you.</p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Moon className="h-4 w-4 text-muted-foreground" /> Appearance
          </CardTitle>
          <CardDescription>LeadBoost is designed dark-first for long working sessions.</CardDescription>
        </CardHeader>
        <CardContent className="flex items-center justify-between pt-0">
          <Label htmlFor="dark-mode" className="text-foreground">
            Dark mode
          </Label>
          <Switch id="dark-mode" checked disabled />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Bell className="h-4 w-4 text-muted-foreground" /> Notifications
          </CardTitle>
          <CardDescription>Notification preferences are coming soon.</CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          <div className="rounded-xl border border-dashed border-white/10 bg-white/[0.02] p-6 text-center text-sm text-muted-foreground">
            You&apos;ll be able to control email and in-app alerts here once notifications ship.
          </div>
        </CardContent>
      </Card>

      <Card className="border-rose-500/20">
        <CardHeader>
          <CardTitle>Session</CardTitle>
          <CardDescription>Sign out of LeadBoost on this device.</CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          <Button variant="destructive" onClick={logout}>
            <LogOut className="h-4 w-4" /> Log out
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}

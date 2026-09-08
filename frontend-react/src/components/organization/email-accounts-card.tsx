"use client";

import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Mail, Plus, ShieldCheck, Trash2, Pencil } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ConfirmDialog } from "@/components/shared/confirm-dialog";
import { EmptyState } from "@/components/shared/empty-state";
import {
  useEmailAccounts,
  useCreateEmailAccount,
  useUpdateEmailAccount,
  useDeleteEmailAccount,
  useVerifyEmailAccount,
} from "@/features/email-accounts/hooks";
import { emailAccountFormSchema, type EmailAccountFormValues } from "@/lib/validation";
import type { EmailAccount, VerificationStatusValue } from "@/types/api";
import { formatDate } from "@/lib/utils";

// P1.3: display treatment for the closed VerificationStatus vocabulary --
// see backend core/domain/models/email_account.py::VerificationStatus.
const VERIFICATION_STYLES: Record<VerificationStatusValue, { label: string; className: string }> = {
  unverified: { label: "Not verified", className: "bg-white/10 text-muted-foreground border-white/10" },
  verified: { label: "Verified", className: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30" },
  failed: { label: "Verification failed", className: "bg-rose-500/15 text-rose-300 border-rose-500/30" },
  requires_reauth: { label: "Requires re-auth", className: "bg-amber-500/15 text-amber-300 border-amber-500/30" },
  disabled: { label: "Disabled", className: "bg-white/10 text-muted-foreground border-white/10" },
};

function VerificationBadge({ status }: { status: VerificationStatusValue }) {
  const style = VERIFICATION_STYLES[status];
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${style.className}`}>
      {style.label}
    </span>
  );
}

const FORM_DEFAULTS: EmailAccountFormValues = {
  email_address: "",
  display_name: "",
  smtp_host: "",
  smtp_port: 587,
  security_mode: "starttls",
  username: "",
  credential_type: "smtp_password",
  credential: "",
};

interface EmailAccountDialogProps {
  orgId: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** undefined = "add" mode; a real account = "edit" mode. */
  account?: EmailAccount;
}

function EmailAccountDialog({ orgId, open, onOpenChange, account }: EmailAccountDialogProps) {
  const isEdit = !!account;
  const createAccount = useCreateEmailAccount(orgId);
  const updateAccount = useUpdateEmailAccount(orgId);

  const {
    register,
    handleSubmit,
    reset,
    setValue,
    watch,
    formState: { errors },
  } = useForm<EmailAccountFormValues>({ resolver: zodResolver(emailAccountFormSchema), defaultValues: FORM_DEFAULTS });

  const securityMode = watch("security_mode");
  const credentialType = watch("credential_type");

  useEffect(() => {
    if (open) {
      // SECURITY: `credential` is always reset to "" here, on every open
      // -- an edit dialog is NEVER pre-populated from the account's
      // (nonexistent, per the API) credential field. The placeholder text
      // below is a fixed visual hint, not returned data.
      reset(
        account
          ? {
              email_address: account.email_address,
              display_name: account.display_name ?? "",
              smtp_host: account.smtp_host,
              smtp_port: account.smtp_port,
              security_mode: account.security_mode,
              username: account.username ?? "",
              credential_type: account.credential_type === "oauth_token" ? "smtp_password" : account.credential_type,
              credential: "",
            }
          : FORM_DEFAULTS
      );
    }
  }, [open, account, reset]);

  const onSubmit = (values: EmailAccountFormValues) => {
    const credential = values.credential?.trim() || undefined; // "" -> omitted, preserves existing on edit
    if (isEdit && account) {
      updateAccount.mutate(
        {
          accountId: account.id,
          payload: {
            display_name: values.display_name || null,
            smtp_host: values.smtp_host,
            smtp_port: values.smtp_port,
            security_mode: values.security_mode,
            username: values.username || null,
            credential_type: values.credential_type,
            ...(credential ? { credential } : {}),
          },
        },
        { onSuccess: () => onOpenChange(false) }
      );
    } else {
      createAccount.mutate(
        {
          email_address: values.email_address,
          display_name: values.display_name || null,
          smtp_host: values.smtp_host,
          smtp_port: values.smtp_port,
          security_mode: values.security_mode,
          username: values.username || null,
          credential_type: values.credential_type,
          ...(credential ? { credential } : {}),
        },
        {
          onSuccess: () => {
            onOpenChange(false);
            reset(FORM_DEFAULTS);
          },
        }
      );
    }
  };

  const pending = createAccount.isPending || updateAccount.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit email account" : "Add email account"}</DialogTitle>
          <DialogDescription>
            Connection details for a sender mailbox. This only verifies the connection — it doesn&apos;t send any
            email.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="email_address">Email address</Label>
            <Input
              id="email_address"
              type="email"
              disabled={isEdit}
              error={!!errors.email_address}
              {...register("email_address")}
            />
            {errors.email_address && <p className="text-xs text-rose-400">{errors.email_address.message}</p>}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="display_name">Display name</Label>
            <Input id="display_name" placeholder="e.g. Sales Outreach" {...register("display_name")} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="smtp_host">SMTP host</Label>
              <Input id="smtp_host" placeholder="smtp.example.com" error={!!errors.smtp_host} {...register("smtp_host")} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="smtp_port">Port</Label>
              <Input
                id="smtp_port"
                type="number"
                error={!!errors.smtp_port}
                {...register("smtp_port", { valueAsNumber: true })}
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="security_mode">Security</Label>
              <Select value={securityMode} onValueChange={(v) => setValue("security_mode", v as "starttls" | "tls")}>
                <SelectTrigger id="security_mode">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="starttls">STARTTLS</SelectItem>
                  <SelectItem value="tls">TLS (implicit)</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="credential_type">Credential type</Label>
              <Select
                value={credentialType}
                onValueChange={(v) => setValue("credential_type", v as "smtp_password" | "app_password")}
              >
                <SelectTrigger id="credential_type">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="smtp_password">SMTP password</SelectItem>
                  <SelectItem value="app_password">App password</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="username">Username (optional)</Label>
            <Input id="username" placeholder="Defaults to the email address" {...register("username")} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="credential">{isEdit ? "New password" : "Password"}</Label>
            <Input
              id="credential"
              type="password"
              autoComplete="new-password"
              placeholder={isEdit ? "Leave blank to keep the current one" : "SMTP or app password"}
              {...register("credential")}
            />
            <p className="text-xs text-muted-foreground">
              {isEdit
                ? "Never shown after saving. Leave blank to keep the existing credential."
                : "Stored encrypted. Never shown again after saving."}
            </p>
          </div>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={pending}>
              {isEdit ? "Save changes" : "Add account"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function EmailAccountsCard({ orgId }: { orgId: number }) {
  const { data: accounts, isLoading } = useEmailAccounts(orgId);
  const deleteAccount = useDeleteEmailAccount(orgId);
  const verifyAccount = useVerifyEmailAccount(orgId);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingAccount, setEditingAccount] = useState<EmailAccount | undefined>(undefined);
  const [disableTarget, setDisableTarget] = useState<EmailAccount | null>(null);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle className="flex items-center gap-2">
            <Mail className="h-4 w-4 text-muted-foreground" /> Email Accounts
          </CardTitle>
          <CardDescription>
            Sender mailboxes your organization can send outreach from. Adding one here only verifies the connection
            — no email is sent yet.
          </CardDescription>
        </div>
        <Button
          size="sm"
          onClick={() => {
            setEditingAccount(undefined);
            setDialogOpen(true);
          }}
        >
          <Plus className="h-4 w-4" /> Add account
        </Button>
      </CardHeader>
      <CardContent className="pt-0">
        {isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : !accounts || accounts.length === 0 ? (
          <EmptyState
            icon={Mail}
            title="No email accounts yet"
            description="Add a sender mailbox to prepare outreach in a later phase."
          />
        ) : (
          <div className="space-y-2">
            {accounts.map((account) => (
              <div
                key={account.id}
                className="flex items-center justify-between gap-4 rounded-xl border border-white/10 bg-white/[0.02] p-3"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium">{account.display_name || account.email_address}</span>
                    <VerificationBadge status={account.verification_status} />
                    {!account.is_active && (
                      <span className="inline-flex items-center rounded-full border border-white/10 bg-white/10 px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
                        Disabled
                      </span>
                    )}
                  </div>
                  <p className="truncate text-sm text-muted-foreground">
                    {account.email_address} · {account.smtp_host}:{account.smtp_port} ·{" "}
                    {account.security_mode === "tls" ? "TLS" : "STARTTLS"}
                  </p>
                  {account.verified_at && (
                    <p className="text-xs text-muted-foreground">Verified {formatDate(account.verified_at)}</p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={!account.is_active}
                    loading={verifyAccount.isPending && verifyAccount.variables === account.id}
                    onClick={() => verifyAccount.mutate(account.id)}
                  >
                    <ShieldCheck className="h-4 w-4" /> Verify
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setEditingAccount(account);
                      setDialogOpen(true);
                    }}
                  >
                    <Pencil className="h-4 w-4" />
                  </Button>
                  {account.is_active && (
                    <Button size="sm" variant="ghost" onClick={() => setDisableTarget(account)}>
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>

      <EmailAccountDialog orgId={orgId} open={dialogOpen} onOpenChange={setDialogOpen} account={editingAccount} />

      <ConfirmDialog
        open={!!disableTarget}
        onOpenChange={(open) => !open && setDisableTarget(null)}
        title="Disable this email account?"
        description={`"${disableTarget?.display_name || disableTarget?.email_address}" will stop appearing as an available sender. Its configuration is kept, and it can be re-enabled later.`}
        confirmLabel="Disable"
        destructive
        loading={deleteAccount.isPending}
        onConfirm={() => {
          if (disableTarget) {
            deleteAccount.mutate(disableTarget.id, { onSuccess: () => setDisableTarget(null) });
          }
        }}
      />
    </Card>
  );
}

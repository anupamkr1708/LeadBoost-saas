"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useCreateSingleLead, useCreateLeadsFromUrls } from "@/features/leads/hooks";
import { useAuthStore } from "@/store/auth-store";
import { singleLeadSchema, bulkLeadsSchema, type SingleLeadValues, type BulkLeadsValues } from "@/lib/validation";

/** Dialog for adding leads manually — either a single website or a batch of URLs. */
export function AddLeadDialog() {
  const [open, setOpen] = useState(false);
  const user = useAuthStore((s) => s.user);
  const createSingle = useCreateSingleLead();
  const createBulk = useCreateLeadsFromUrls();

  const singleForm = useForm<SingleLeadValues>({ resolver: zodResolver(singleLeadSchema) });
  const bulkForm = useForm<BulkLeadsValues>({ resolver: zodResolver(bulkLeadsSchema), defaultValues: { message_style: "professional" } });

  const onSingleSubmit = (values: SingleLeadValues) => {
    if (!user?.organization_id) return;
    createSingle.mutate(
      { website: values.website, organization_id: user.organization_id, owner_id: user.id },
      { onSuccess: () => { setOpen(false); singleForm.reset(); } }
    );
  };

  const onBulkSubmit = (values: BulkLeadsValues) => {
    const urls = values.urls
      .split(/[\n,]/)
      .map((u) => u.trim())
      .filter(Boolean);
    createBulk.mutate(
      { urls, message_style: values.message_style },
      { onSuccess: () => { setOpen(false); bulkForm.reset(); } }
    );
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus className="h-4 w-4" /> Add lead
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add leads</DialogTitle>
          <DialogDescription>Add a single website or upload a batch — each is run through the enrichment pipeline.</DialogDescription>
        </DialogHeader>

        <Tabs defaultValue="single">
          <TabsList>
            <TabsTrigger value="single">Single lead</TabsTrigger>
            <TabsTrigger value="batch">Batch upload</TabsTrigger>
          </TabsList>

          <TabsContent value="single">
            <form onSubmit={singleForm.handleSubmit(onSingleSubmit)} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="website">Website URL</Label>
                <Input id="website" placeholder="acmeshoes.com" error={!!singleForm.formState.errors.website} {...singleForm.register("website")} />
                {singleForm.formState.errors.website && (
                  <p className="text-xs text-rose-400">{singleForm.formState.errors.website.message}</p>
                )}
              </div>
              <DialogFooter>
                <Button type="submit" loading={createSingle.isPending}>
                  Add lead
                </Button>
              </DialogFooter>
            </form>
          </TabsContent>

          <TabsContent value="batch">
            <form onSubmit={bulkForm.handleSubmit(onBulkSubmit)} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="urls">Website URLs</Label>
                <Textarea id="urls" placeholder={"acmeshoes.com\nbloomandsole.com\nmarinakicks.in"} rows={6} {...bulkForm.register("urls")} />
                <p className="text-xs text-muted-foreground">One URL per line, or comma-separated.</p>
              </div>
              <DialogFooter>
                <Button type="submit" loading={createBulk.isPending}>
                  Import batch
                </Button>
              </DialogFooter>
            </form>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}

import { z } from "zod";

/** Shared form schemas (react-hook-form + zod), grouped by feature. */

export const loginSchema = z.object({
  email: z.string().min(1, "Email is required").email("Enter a valid email address"),
  password: z.string().min(1, "Password is required"),
});
export type LoginValues = z.infer<typeof loginSchema>;

export const registerSchema = z
  .object({
    firstName: z.string().min(1, "First name is required"),
    lastName: z.string().min(1, "Last name is required"),
    email: z.string().min(1, "Email is required").email("Enter a valid email address"),
    password: z.string().min(8, "Password must be at least 8 characters"),
    confirmPassword: z.string().min(1, "Please confirm your password"),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "Passwords don't match",
    path: ["confirmPassword"],
  });
export type RegisterValues = z.infer<typeof registerSchema>;

export const forgotPasswordSchema = z.object({
  email: z.string().min(1, "Email is required").email("Enter a valid email address"),
});
export type ForgotPasswordValues = z.infer<typeof forgotPasswordSchema>;

export const discoverySearchSchema = z.object({
  query: z.string().min(3, "Describe what you're looking for (min 3 characters)").max(200, "Keep it under 200 characters"),
  limit: z.number().int().min(1).max(50).optional(),
});
export type DiscoverySearchValues = z.infer<typeof discoverySearchSchema>;

export const singleLeadSchema = z.object({
  website: z.string().min(1, "A website URL is required"),
});
export type SingleLeadValues = z.infer<typeof singleLeadSchema>;

export const bulkLeadsSchema = z.object({
  urls: z.string().min(1, "Add at least one URL"),
  message_style: z.string().default("professional"),
});
export type BulkLeadsValues = z.infer<typeof bulkLeadsSchema>;

export const leadEditSchema = z.object({
  company_name: z.string().nullable().optional(),
  industry: z.string().nullable().optional(),
  about_text: z.string().nullable().optional(),
  contact_name: z.string().nullable().optional(),
  contact_title: z.string().nullable().optional(),
  email: z.string().email("Enter a valid email").nullable().optional().or(z.literal("")),
  phone: z.string().nullable().optional(),
  address: z.string().nullable().optional(),
  linkedin_url: z.string().nullable().optional(),
  twitter_url: z.string().nullable().optional(),
  facebook_url: z.string().nullable().optional(),
});
export type LeadEditValues = z.infer<typeof leadEditSchema>;

export const orgEditSchema = z.object({
  name: z.string().min(1, "Organization name is required"),
  description: z.string().nullable().optional(),
  // P1.2 (Company Profile)
  industry: z.string().nullable().optional(),
  icp_description: z.string().nullable().optional(),
});
export type OrgEditValues = z.infer<typeof orgEditSchema>;

// P1.2: organization qualification threshold, same 0–100 scale as
// Lead.score (see backend core/domain/models/qualification_settings.py).
export const qualificationSettingsSchema = z.object({
  qualification_threshold: z
    .number({ invalid_type_error: "Enter a number between 0 and 100" })
    .min(0, "Must be at least 0")
    .max(100, "Must be at most 100"),
});
export type QualificationSettingsValues = z.infer<typeof qualificationSettingsSchema>;

export const profileEditSchema = z.object({
  first_name: z.string().nullable().optional(),
  last_name: z.string().nullable().optional(),
  // P1.2 (Sender Profile)
  job_title: z.string().nullable().optional(),
  signature: z.string().nullable().optional(),
});
export type ProfileEditValues = z.infer<typeof profileEditSchema>;

// P1.3: Email Account form. `credential` is intentionally NOT required
// here even on create -- an account can be saved with connection
// metadata only and have its credential added in a follow-up edit; the
// backend enforces "no credential -> verification fails deterministically"
// rather than this form enforcing "credential required to save".
export const emailAccountFormSchema = z.object({
  email_address: z.string().email("Enter a valid email address"),
  display_name: z.string().nullable().optional(),
  smtp_host: z.string().min(1, "SMTP host is required"),
  smtp_port: z
    .number({ invalid_type_error: "Enter a port number" })
    .int()
    .min(1, "Must be between 1 and 65535")
    .max(65535, "Must be between 1 and 65535"),
  security_mode: z.enum(["starttls", "tls"]),
  username: z.string().nullable().optional(),
  credential_type: z.enum(["smtp_password", "app_password"]),
  // Write-only. Left blank on an edit -> existing credential preserved.
  credential: z.string().nullable().optional(),
});
export type EmailAccountFormValues = z.infer<typeof emailAccountFormSchema>;

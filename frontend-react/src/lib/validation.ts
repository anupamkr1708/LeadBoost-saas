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
});
export type OrgEditValues = z.infer<typeof orgEditSchema>;

export const profileEditSchema = z.object({
  first_name: z.string().nullable().optional(),
  last_name: z.string().nullable().optional(),
});
export type ProfileEditValues = z.infer<typeof profileEditSchema>;

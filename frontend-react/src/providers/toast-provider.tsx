"use client";

import { Toaster } from "react-hot-toast";

export function ToastProvider() {
  return (
    <Toaster
      position="top-right"
      toastOptions={{
        duration: 4000,
        style: {
          background: "rgba(18, 16, 28, 0.95)",
          color: "#F5F3FF",
          border: "1px solid rgba(255,255,255,0.09)",
          borderRadius: "0.875rem",
          backdropFilter: "blur(16px)",
          fontSize: "0.875rem",
          boxShadow: "0 20px 60px -15px rgba(0,0,0,0.6)",
        },
        success: { iconTheme: { primary: "#10B981", secondary: "#0D0B14" } },
        error: { iconTheme: { primary: "#F43F5E", secondary: "#0D0B14" } },
      }}
    />
  );
}

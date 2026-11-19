import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MailerConversationMessage, OutreachMailerState } from "@/types/api";

const { api } = vi.hoisted(() => ({ api: { getMailerState: vi.fn() } }));
vi.mock("@/features/outreach/api", () => ({ outreachApi: api }));

import { MailerStatePanel } from "@/components/outreach/mailer-state-panel";

const GOOD = "0123456789abcdef0123456789abcdef";
const OTHER = "fedcba9876543210fedcba9876543210";

function msg(over: Partial<MailerConversationMessage>): MailerConversationMessage {
  return {
    direction: "inbound", subject: "Re: Hello", body: "Thanks, tell me more.", body_truncated: false,
    created_at: "2026-10-31T09:00:00Z", delivery_state: null, message_reference: GOOD, ...over,
  };
}

function state(messages: MailerConversationMessage[]): OutreachMailerState {
  return {
    availability: "available", error_code: null,
    mailer: { delivery_state: "sent", updated_at: "2026-10-31T09:00:00Z", messages, has_more: false },
  };
}

function renderPanel(messages: MailerConversationMessage[], onReply?: (m: MailerConversationMessage) => void) {
  api.getMailerState.mockResolvedValue(state(messages));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MailerStatePanel actionId={5} leadboostState="submitted" onReply={onReply} />
    </QueryClientProvider>
  );
}

beforeEach(() => api.getMailerState.mockReset());

describe("Reply action on conversation messages", () => {
  it("is offered on an inbound message with a valid reference", async () => {
    renderPanel([msg({})], vi.fn());
    expect(await screen.findByRole("button", { name: /^reply$/i })).toBeInTheDocument();
  });

  it("is never offered on an outbound message -- even one that (wrongly) carries a reference", async () => {
    renderPanel(
      [msg({ direction: "outbound", delivery_state: "sent", message_reference: GOOD, body: "Our outbound text" })],
      vi.fn()
    );
    await screen.findByText("Our outbound text");
    expect(screen.queryByRole("button", { name: /reply/i })).toBeNull();
  });

  it.each([
    ["null", null], ["absent", undefined], ["empty", ""], ["too short", GOOD.slice(1)], ["uppercase", GOOD.toUpperCase()],
    ["not hex", "z".repeat(32)], ["an RFC Message-ID", "<abc@example.com>"],
  ])("is not offered when the reference is %s", async (_name, ref) => {
    renderPanel([msg({ message_reference: ref as string | null | undefined, body: "Inbound text" })], vi.fn());
    await screen.findByText("Inbound text");
    expect(screen.queryByRole("button", { name: /reply/i })).toBeNull();
  });

  it("is not offered at all when no onReply handler is given (the panel stays purely read-only)", async () => {
    renderPanel([msg({ body: "Inbound text" })]);
    await screen.findByText("Inbound text");
    expect(screen.queryByRole("button", { name: /reply/i })).toBeNull();
  });

  it("reports exactly the message that was selected, not the latest or a neighbour", async () => {
    const onReply = vi.fn();
    renderPanel(
      [
        msg({ body: "First inbound", message_reference: GOOD, created_at: "2026-10-31T08:00:00Z" }),
        msg({ direction: "outbound", delivery_state: "sent", body: "Our answer", message_reference: null }),
        msg({ body: "Second inbound", message_reference: OTHER, created_at: "2026-10-31T10:00:00Z" }),
      ],
      onReply
    );
    const buttons = await screen.findAllByRole("button", { name: /^reply$/i });
    expect(buttons).toHaveLength(2);                       // one per inbound message, none for the outbound
    await userEvent.click(buttons[0]!);
    expect(onReply).toHaveBeenCalledTimes(1);
    expect(onReply.mock.calls[0]![0]).toMatchObject({ body: "First inbound", message_reference: GOOD });
  });

  it("keeps the opaque reference as data: it is never displayed", async () => {
    const { container } = renderPanel([msg({})], vi.fn());
    await screen.findByRole("button", { name: /^reply$/i });
    expect(container.textContent).not.toContain(GOOD);
  });
});

describe("untrusted inbound text", () => {
  it("is rendered as plain text and never executed", async () => {
    const evil = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>';
    const { container } = renderPanel([msg({ body: evil, subject: evil })], vi.fn());
    await screen.findByRole("button", { name: /^reply$/i });
    expect(container.querySelector("img, script, iframe")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
    expect(container.textContent).toContain("<script>window.__pwned=1</script>");
  });
});

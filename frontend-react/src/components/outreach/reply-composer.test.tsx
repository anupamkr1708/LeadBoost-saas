import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AxiosError, type AxiosResponse } from "axios";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Lead, OutreachAction, ReplyDraft, ReplyDraftState } from "@/types/api";

const { api, toast } = vi.hoisted(() => {
  const toastFn = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return {
    toast: toastFn,
    api: {
      createReply: vi.fn(), startReplyDraft: vi.fn(), getReplyDraft: vi.fn(), approveReply: vi.fn(),
      dispatch: vi.fn(), cancel: vi.fn(), getMailerState: vi.fn(), list: vi.fn(),
    },
  };
});
vi.mock("@/features/outreach/api", () => ({ outreachApi: api }));
vi.mock("react-hot-toast", () => ({ default: toast }));

import { ReplyComposer, type ReplyComposerProps } from "@/components/outreach/reply-composer";

const REF = "0123456789abcdef0123456789abcdef";
const DIGEST = "b".repeat(64);
const lead = { id: 3, company_name: "Acme", contact_name: "Jane Doe", email: "jane@acme.test", website: "https://acme.test" } as unknown as Lead;

function action(over: Partial<OutreachAction> = {}): OutreachAction {
  return {
    id: 7, organization_id: 1, lead_id: 3, email_account_id: 2, mode: "manual", state: "pending_review",
    recipient_email: "jane@acme.test", recipient_name: "Jane Doe", subject: null, body: "", correlation_id: null,
    idempotency_key: "reply:abc", reason: null, approved_by_user_id: null, approved_at: null, submitted_at: null,
    cancelled_at: null, mailing_agent_reference: null, dispatch_attempts: 0, last_dispatch_error: null,
    reply_to_message_reference: REF, reply_draft_requested_at: "2026-10-31T10:00:00Z", approved_content_sha256: null,
    approved_draft_version: null, created_at: "2026-10-31T10:00:00Z", updated_at: null, ...over,
  };
}

function draft(state: ReplyDraftState, over: Partial<ReplyDraft> = {}): ReplyDraft {
  const content = !["not_started", "generating", "failed", "cancelled"].includes(state);
  return {
    state, subject: content ? "Re: Pricing question" : null,
    body: content ? "Hi Jane,\n\nHere is the exact reply text.\n\nBest,\nSender" : null,
    content_sha256: content ? DIGEST : null, draft_version: content ? 1 : null, updated_at: "2026-10-31T10:01:00Z", ...over,
  };
}

function renderComposer(over: Partial<ReplyComposerProps> = {}, ui?: (p: ReplyComposerProps) => ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  const props: ReplyComposerProps = {
    lead, senderAccountId: 2, senderLabel: "Sales <sales@sender.test>", messageReference: REF,
    inboundSubject: "Pricing question", inboundBody: "How much does it cost?", inboundBodyTruncated: false, inboundReceivedAt: "2026-10-31T09:00:00Z",
    conversationActionId: 11, replyAction: action(), onClose: vi.fn(), ...over,
  };
  const utils = render(<QueryClientProvider client={client}>{ui ? ui(props) : <ReplyComposer {...props} />}</QueryClientProvider>);
  return { ...utils, props, client };
}

const httpError = (status: number, data: unknown) =>
  new AxiosError("x", String(status), undefined, undefined, { status, data, statusText: "", headers: {}, config: {} } as unknown as AxiosResponse);

const btn = (name: RegExp) => screen.queryByRole("button", { name });

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  toast.mockReset(); toast.success.mockReset(); toast.error.mockReset();
});

describe("what the user sees", () => {
  it("shows the message being answered, the recipient from the lead, and the sender mailbox", async () => {
    api.getReplyDraft.mockResolvedValue(draft("awaiting_approval"));
    renderComposer();
    expect(screen.getByText("How much does it cost?")).toBeInTheDocument();
    expect(screen.getAllByText("Pricing question").length).toBeGreaterThan(0);
    expect(screen.getByText(/Jane Doe <jane@acme.test>/)).toBeInTheDocument();
    expect(screen.getByText("Sales <sales@sender.test>")).toBeInTheDocument();
    await screen.findByText("Draft ready for review");
  });

  it("shows the EXACT persisted subject and body, complete, once the draft is ready", async () => {
    api.getReplyDraft.mockResolvedValue(draft("awaiting_approval"));
    renderComposer();
    await screen.findByText("Draft ready for review");
    expect(screen.getByText("Re: Pricing question", { selector: "p.font-medium" })).toBeInTheDocument();
    const body = screen.getByText(/Here is the exact reply text\./);
    expect(body.textContent).toBe("Hi Jane,\n\nHere is the exact reply text.\n\nBest,\nSender");
    expect(screen.getByText(/exact message that will be sent if you approve it/i)).toBeInTheDocument();
  });

  it("never renders the opaque message reference or the content digest", async () => {
    api.getReplyDraft.mockResolvedValue(draft("awaiting_approval"));
    const { container } = renderComposer();
    await screen.findByText("Draft ready for review");
    expect(container.textContent).not.toContain(REF);
    expect(container.textContent).not.toContain(DIGEST);
  });
});

describe("gating: approve, then send", () => {
  it("offers neither approve nor send while the draft is still generating", async () => {
    api.getReplyDraft.mockResolvedValue(draft("generating"));
    renderComposer();
    await screen.findByText("Generating reply");
    expect(btn(/approve exact draft/i)).toBeNull();
    expect(btn(/send approved reply/i)).toBeNull();
  });

  it("offers approve (not send) once the draft is ready, and send only after approval", async () => {
    api.getReplyDraft.mockResolvedValue(draft("awaiting_approval"));
    const first = renderComposer();
    await screen.findByText("Draft ready for review");
    expect(btn(/approve exact draft/i)).toBeEnabled();
    expect(btn(/send approved reply/i)).toBeNull();
    first.unmount();

    renderComposer({ replyAction: action({ state: "approved", approved_content_sha256: DIGEST, approved_draft_version: 1 }) });
    await screen.findByText("Approved");
    expect(btn(/send approved reply/i)).toBeEnabled();
    expect(btn(/approve exact draft/i)).toBeNull();
    expect(screen.getByText(/Nothing has been sent yet/i)).toBeInTheDocument();
  });

  it("does not offer send for an 'approved' action that carries no approved digest", async () => {
    api.getReplyDraft.mockResolvedValue(draft("awaiting_approval"));
    renderComposer({ replyAction: action({ state: "approved", approved_content_sha256: null }) });
    await screen.findByText("Approved");
    expect(btn(/send approved reply/i)).toBeDisabled();   // present, but not usable without stored approval evidence
  });

  it("approving sends the digest of the text on screen and sends nothing else", async () => {
    api.getReplyDraft.mockResolvedValue(draft("awaiting_approval"));
    api.approveReply.mockResolvedValue(action({ state: "approved", approved_content_sha256: DIGEST }));
    renderComposer();
    await screen.findByText("Draft ready for review");
    await userEvent.click(btn(/approve exact draft/i)!);
    await waitFor(() => expect(api.approveReply).toHaveBeenCalledTimes(1));
    expect(api.approveReply).toHaveBeenCalledWith(7, DIGEST);
    expect(api.dispatch).not.toHaveBeenCalled();           // approval releases nothing
    expect(api.startReplyDraft).not.toHaveBeenCalled();
  });
});

describe("duplicate-click protection", () => {
  it("a double-click on Approve approves once", async () => {
    api.getReplyDraft.mockResolvedValue(draft("awaiting_approval"));
    api.approveReply.mockImplementation(() => new Promise((r) => setTimeout(() => r(action({ state: "approved" })), 60)));
    renderComposer();
    await screen.findByText("Draft ready for review");
    await userEvent.dblClick(btn(/approve exact draft/i)!);
    await waitFor(() => expect(api.approveReply).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 120));
    expect(api.approveReply).toHaveBeenCalledTimes(1);
  });

  it("a double-click on Send sends once", async () => {
    api.getReplyDraft.mockResolvedValue(draft("awaiting_approval"));
    api.dispatch.mockImplementation(() => new Promise((r) => setTimeout(() => r(action({ state: "submitted" })), 60)));
    renderComposer({ replyAction: action({ state: "approved", approved_content_sha256: DIGEST }) });
    await screen.findByText("Approved");
    await userEvent.dblClick(btn(/send approved reply/i)!);
    await waitFor(() => expect(api.dispatch).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 120));
    expect(api.dispatch).toHaveBeenCalledTimes(1);
    expect(api.dispatch).toHaveBeenCalledWith(7);
  });

  it("disables every mutating control while a request is in flight", async () => {
    api.getReplyDraft.mockResolvedValue(draft("awaiting_approval"));
    let release: (a: OutreachAction) => void = () => {};
    api.approveReply.mockImplementation(() => new Promise((r) => { release = r; }));
    renderComposer();
    await screen.findByText("Draft ready for review");
    await userEvent.click(btn(/approve exact draft/i)!);
    await waitFor(() => expect(btn(/approve exact draft/i)).toBeDisabled());
    expect(btn(/cancel reply/i)).toBeNull();               // not offered while busy
    release(action({ state: "approved" }));
  });
});

describe("failures are safe and recoverable", () => {
  it("a stale draft is refused: the user is told, the draft is re-read, and nothing is sent", async () => {
    api.getReplyDraft.mockResolvedValue(draft("awaiting_approval"));
    api.approveReply.mockRejectedValue(httpError(409, { detail: { error_code: "reply_draft_stale", message: "changed" } }));
    renderComposer();
    await screen.findByText("Draft ready for review");
    const reads = api.getReplyDraft.mock.calls.length;
    await userEvent.click(btn(/approve exact draft/i)!);
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.error.mock.calls[0]![0]).toMatch(/changed after you reviewed it/i);
    await waitFor(() => expect(api.getReplyDraft.mock.calls.length).toBeGreaterThan(reads));
    expect(api.dispatch).not.toHaveBeenCalled();
  });

  it("a failed generation shows plainly, offers no approve or send, and nothing was sent", async () => {
    api.getReplyDraft.mockResolvedValue(draft("failed"));
    renderComposer();
    await screen.findByText("Generation failed");
    expect(screen.getByText(/nothing was sent/i)).toBeInTheDocument();
    expect(btn(/approve exact draft/i)).toBeNull();
    expect(btn(/send approved reply/i)).toBeNull();
    expect(btn(/cancel reply/i)).toBeEnabled();
  });

  it("an unreadable draft is a calm, retryable state, not raw error text", async () => {
    api.getReplyDraft.mockRejectedValue(httpError(502, { detail: { error_code: "reply_draft_unavailable", message: "Traceback secret-host.internal" } }));
    const { container } = renderComposer();
    // The read is retried twice with backoff (1 s, then 2 s) before this state is shown -- bounded, by design.
    await screen.findByText("Draft unavailable", {}, { timeout: 6000 });
    expect(container.textContent).not.toMatch(/Traceback|secret-host|502/);
    expect(btn(/try again/i)).toBeEnabled();
    expect(btn(/approve exact draft/i)).toBeNull();
  });

  it("a release that did not complete offers a retry with the same approval", async () => {
    api.getReplyDraft.mockResolvedValue(draft("awaiting_approval"));
    renderComposer({ replyAction: action({ state: "dispatch_failed", approved_content_sha256: DIGEST, dispatch_attempts: 1 }) });
    await screen.findByText("Not sent yet");
    expect(btn(/try sending again/i)).toBeEnabled();
  });
});

describe("outcomes", () => {
  const submitted = () => action({ state: "submitted", approved_content_sha256: DIGEST, submitted_at: "2026-10-31T10:05:00Z" });

  it("Unknown: says delivery is unconfirmed, says not to resend, and offers no send or cancel", async () => {
    api.getReplyDraft.mockResolvedValue(draft("unknown"));
    renderComposer({ replyAction: submitted() });
    await screen.findByText("Delivery unconfirmed");
    expect(screen.getByText(/don.t send it again/i)).toBeInTheDocument();
    expect(btn(/send/i)).toBeNull();
    expect(btn(/cancel reply/i)).toBeNull();
  });

  it("Sent: says SMTP accepted it, and is honest that this is not proof of inbox delivery", async () => {
    api.getReplyDraft.mockResolvedValue(draft("sent"));
    renderComposer({ replyAction: submitted() });
    await screen.findByText("Sent (SMTP accepted)");
    expect(screen.getByText(/doesn.t confirm it reached the recipient/i)).toBeInTheDocument();
    expect(screen.getByText(/This is the message that was approved/i)).toBeInTheDocument();
  });

  it("Queued is not shown as Sent", async () => {
    api.getReplyDraft.mockResolvedValue(draft("queued"));
    renderComposer({ replyAction: submitted() });
    await screen.findByText("Ready to send");
    expect(screen.queryByText(/Sent \(SMTP accepted\)/)).toBeNull();
  });

  it("Cancelled shows as cancelled and offers nothing", async () => {
    renderComposer({ replyAction: action({ state: "cancelled" }) });
    await screen.findByText("Cancelled");
    expect(btn(/approve|send|cancel reply/i)).toBeNull();
  });
});

describe("cancellation", () => {
  it("cancelling a reply before sending calls cancel for that action", async () => {
    api.getReplyDraft.mockResolvedValue(draft("awaiting_approval"));
    api.cancel.mockResolvedValue(action({ state: "cancelled" }));
    renderComposer();
    await screen.findByText("Draft ready for review");
    await userEvent.click(btn(/cancel reply/i)!);
    await waitFor(() => expect(api.cancel).toHaveBeenCalledTimes(1));
    expect(api.cancel).toHaveBeenCalledWith(7);
    expect(api.dispatch).not.toHaveBeenCalled();
  });
});

describe("starting a reply", () => {
  it("creates exactly one reply for the selected message, then asks for the draft -- and sends nothing", async () => {
    api.createReply.mockResolvedValue(action({ reply_draft_requested_at: null }));
    api.startReplyDraft.mockResolvedValue(draft("generating"));
    api.getReplyDraft.mockResolvedValue(draft("generating"));
    renderComposer({ replyAction: null });
    expect(screen.getByText("Not started")).toBeInTheDocument();
    await userEvent.click(btn(/generate reply draft/i)!);
    await waitFor(() => expect(api.startReplyDraft).toHaveBeenCalledWith(7));
    expect(api.createReply).toHaveBeenCalledTimes(1);
    expect(api.createReply).toHaveBeenCalledWith({
      lead_id: 3, email_account_id: 2, reply_to_message_reference: REF, mode: "manual",
    });
    expect(api.approveReply).not.toHaveBeenCalled();
    expect(api.dispatch).not.toHaveBeenCalled();
  });

  it("a double-click on Generate creates one reply", async () => {
    api.createReply.mockImplementation(() => new Promise((r) => setTimeout(() => r(action({ reply_draft_requested_at: null })), 60)));
    api.startReplyDraft.mockResolvedValue(draft("generating"));
    api.getReplyDraft.mockResolvedValue(draft("generating"));
    renderComposer({ replyAction: null });
    await userEvent.dblClick(btn(/generate reply draft/i)!);
    await waitFor(() => expect(api.startReplyDraft).toHaveBeenCalled());
    expect(api.createReply).toHaveBeenCalledTimes(1);
  });

  it("cannot generate without a verified sender mailbox", () => {
    renderComposer({ replyAction: null, senderAccountId: null, senderLabel: null });
    expect(btn(/generate reply draft/i)).toBeDisabled();
    expect(screen.getByText(/verify a sender mailbox/i)).toBeInTheDocument();
  });
});

describe("a shortened inbound message is never presented as the whole message", () => {
  it("tells the user the original message preview is abbreviated, and keeps the generated draft complete", async () => {
    api.getReplyDraft.mockResolvedValue(draft("awaiting_approval"));
    renderComposer({ inboundBody: "The first part of a long message", inboundBodyTruncated: true });
    await screen.findByText("Draft ready for review");
    const note = screen.getByRole("note");
    expect(note).toHaveTextContent(/shortened preview/i);
    expect(note).toHaveTextContent(/only part of the original message/i);
    expect(screen.getByText("The first part of a long message")).toBeInTheDocument();
    // the generated reply is displayed in full and is not described as shortened
    expect(screen.getByText(/Here is the exact reply text\./)).toBeInTheDocument();
    expect(screen.queryByText(/Message shortened/)).toBeNull();
  });

  it("shows no such notice when the inbound message is complete", async () => {
    api.getReplyDraft.mockResolvedValue(draft("awaiting_approval"));
    renderComposer({ inboundBodyTruncated: false });
    await screen.findByText("Draft ready for review");
    expect(screen.queryByRole("note")).toBeNull();
    expect(screen.queryByText(/shortened preview/i)).toBeNull();
  });

  it("still lets the user approve exactly the displayed draft (the notice is informational, not a gate)", async () => {
    api.getReplyDraft.mockResolvedValue(draft("awaiting_approval"));
    renderComposer({ inboundBodyTruncated: true });
    await screen.findByText("Draft ready for review");
    expect(btn(/approve exact draft/i)).toBeEnabled();
  });
});

describe("untrusted text is never executed", () => {
  const evil = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script><b>bold</b>';

  it("renders the inbound message and the generated draft as plain text", async () => {
    api.getReplyDraft.mockResolvedValue(draft("awaiting_approval", { subject: `Re: ${evil}`, body: `Hi ${evil}` }));
    const { container } = renderComposer({ inboundSubject: evil, inboundBody: evil });
    await screen.findByText("Draft ready for review");
    expect(container.querySelector("img, script, b, iframe")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
    expect(container.textContent).toContain('<img src=x onerror="window.__pwned=1">');
    expect(container.textContent).toContain("<script>window.__pwned=1</script>");
  });
});

describe("replies created before exact-content review existed (C9.4)", () => {
  const legacy = (state: OutreachAction["state"]) =>
    action({ state, reply_draft_requested_at: null, approved_content_sha256: null, dispatch_attempts: state === "approved" ? 0 : 1 });

  it("an old approved reply is shown honestly, offers nothing but Cancel, and never reads as ready to send", async () => {
    renderComposer({ replyAction: legacy("approved") });
    expect(screen.getByText("Earlier reply (not reviewed)")).toBeInTheDocument();
    expect(screen.getByText(/created before exact-content review existed/i)).toBeInTheDocument();
    expect(screen.queryByText(/Ready to send/)).toBeNull();
    expect(btn(/approve exact draft/i)).toBeNull();
    expect(btn(/send approved reply|try sending again/i)).toBeNull();
    expect(btn(/generate reply draft/i)).toBeNull();
    expect(btn(/cancel reply/i)).toBeEnabled();
    expect(api.getReplyDraft).not.toHaveBeenCalled();      // there is no draft to read
  });

  it("an old submitted reply offers no action at all", () => {
    renderComposer({ replyAction: legacy("submitted") });
    expect(screen.getByText("Earlier reply (not reviewed)")).toBeInTheDocument();
    expect(btn(/cancel reply|approve|send/i)).toBeNull();
    expect(api.getReplyDraft).not.toHaveBeenCalled();
  });
});

describe("a draft too large to review in full", () => {
  it("says so plainly, offers no retry, and still allows cancelling", async () => {
    api.getReplyDraft.mockRejectedValue(httpError(422, { detail: { error_code: "reply_draft_too_large", message: "x" } }));
    renderComposer();
    await screen.findByText(/too long to show in full/i);
    expect(btn(/try again/i)).toBeNull();                   // a retry can never fix this
    expect(btn(/approve exact draft/i)).toBeNull();
    expect(btn(/cancel reply/i)).toBeEnabled();
  });
});

describe("a failed start", () => {
  it("surfaces the real error and does not pretend the reply is generating", async () => {
    api.createReply.mockResolvedValue(action({ reply_draft_requested_at: null }));
    api.startReplyDraft.mockRejectedValue(httpError(502, { detail: { error_code: "reply_draft_unavailable", message: "The reply draft is temporarily unavailable. Try again." } }));
    renderComposer({ replyAction: null });
    await userEvent.click(btn(/generate reply draft/i)!);
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.error.mock.calls[0]![0]).toMatch(/temporarily unavailable/i);
    expect(screen.queryByText("Generating reply")).toBeNull();
    expect(api.approveReply).not.toHaveBeenCalled();
    expect(api.dispatch).not.toHaveBeenCalled();
  });
});

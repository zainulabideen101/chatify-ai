import { useState, useEffect, useRef } from "react";
import { PageHeader } from "@/components/Layout";
import { Pill, useFetch } from "@/components/bits";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { PaperPlaneRight, UserSwitch, Sparkle, Robot, Check, Checks } from "@phosphor-icons/react";

// WhatsApp-style status tick for outbound messages.
function StatusTicks({ status }) {
  if (!status || status === "queued" || status === "pending") {
    return <Check size={13} className="opacity-70" />;
  }
  if (status === "sent") return <Check size={13} className="opacity-90" />;
  if (status === "delivered") return <Checks size={14} className="opacity-90" />;
  if (status === "read") return <Checks size={14} className="text-sky-300" weight="bold" />;
  if (status === "failed") return <span className="text-[10px] text-red-300">failed</span>;
  return <Check size={13} className="opacity-70" />;
}

export default function Conversations() {
  const { data: convos, loading, reload: reloadConvos } = useFetch("/conversations");
  const [activeId, setActiveId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const bottomRef = useRef(null);
  const scrollBoxRef = useRef(null);
  const lastMsgIdRef = useRef(null);

  useEffect(() => {
    if (convos && convos.length && !activeId) setActiveId(convos[0].id);
  }, [convos, activeId]);

  const loadDetail = (id) => api.get(`/conversations/${id}`).then((r) => setDetail(r.data));
  useEffect(() => { if (activeId) loadDetail(activeId); /* eslint-disable-next-line */ }, [activeId]);
  useEffect(() => {
    if (!activeId) return undefined;
    const timer = setInterval(() => { reloadConvos(); loadDetail(activeId); }, 5000);
    return () => clearInterval(timer);
    /* eslint-disable-next-line */
  }, [activeId]);

  // Auto-scroll to latest message when messages change (only if a new message arrived).
  useEffect(() => {
    const msgs = detail?.messages;
    if (!msgs || msgs.length === 0) return;
    const lastId = msgs[msgs.length - 1].id;
    if (lastId !== lastMsgIdRef.current) {
      lastMsgIdRef.current = lastId;
      requestAnimationFrame(() => {
        bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
      });
    }
  }, [detail?.messages]);

  // On thread switch, jump to bottom instantly.
  useEffect(() => {
    lastMsgIdRef.current = null;
    if (scrollBoxRef.current) scrollBoxRef.current.scrollTop = scrollBoxRef.current.scrollHeight;
  }, [activeId]);

  const send = async () => {
    const body = draft.trim();
    if (!body || !activeId || sending) return;
    setSending(true);
    try {
      const { data } = await api.post(`/conversations/${activeId}/messages`, { body });
      setDraft("");
      setDetail((current) => current ? { ...current, last_message: body, messages: [...(current.messages || []), data.message] } : current);
      await Promise.all([loadDetail(activeId), reloadConvos()]);
      if (!data.delivery.delivered) toast.info("Saved. WhatsApp not connected — message not delivered.");
    } catch { toast.error("Failed to send"); }
    finally { setSending(false); }
  };

  const takeover = async (returnAi) => {
    const url = returnAi ? "return-to-ai" : "takeover";
    await api.post(`/conversations/${activeId}/${url}`);
    loadDetail(activeId);
    reloadConvos();
    toast.success(returnAi ? "Returned to AI" : "You have taken over");
  };

  if (loading) return <><PageHeader title="Conversations" /><div className="p-8">Loading…</div></>;

  return (
    <>
      <PageHeader title="Conversations" subtitle="Unified WhatsApp inbox" />
      <div className="flex h-[calc(100vh-4rem)]">
        {/* List */}
        <div className="w-80 shrink-0 border-r border-border/60 overflow-y-auto">
          {convos.length === 0 && <div className="p-6 text-sm text-muted-foreground">No conversations yet. They appear once customers message your WhatsApp.</div>}
          {convos.map((c) => {
            const isActive = activeId === c.id;
            const unread = Number(c.unread_count || 0);
            const hasUnread = unread > 0 && !isActive;
            return (
              <button
                key={c.id}
                data-testid={`conversation-${c.id}`}
                onClick={() => setActiveId(c.id)}
                className={`w-full text-left px-4 py-3 border-b border-border/60 transition-colors ${isActive ? "bg-muted" : hasUnread ? "bg-emerald-50/70 hover:bg-emerald-50" : "hover:bg-muted/50"}`}
              >
                <div className="flex items-center justify-between mb-1 gap-2">
                  <span className={`text-sm truncate ${hasUnread ? "font-semibold text-foreground" : "font-medium"}`}>{c.customer_name}</span>
                  <div className="flex items-center gap-1 shrink-0">
                    {c.needs_attention && <span className="h-2 w-2 rounded-full bg-amber-500" />}
                    {hasUnread && (
                      <span
                        data-testid={`unread-badge-${c.id}`}
                        className="min-w-[18px] h-[18px] px-1 rounded-full bg-[hsl(var(--whatsapp))] text-white text-[10px] font-semibold flex items-center justify-center"
                      >
                        {unread > 99 ? "99+" : unread}
                      </span>
                    )}
                  </div>
                </div>
                <p className={`text-xs truncate ${hasUnread ? "text-foreground/80 font-medium" : "text-muted-foreground"}`}>{c.last_message}</p>
                <div className="mt-1.5"><Pill tone={c.mode}>{c.mode}</Pill></div>
              </button>
            );
          })}
        </div>

        {/* Thread */}
        <div className="flex-1 flex flex-col min-w-0 bg-[#f7f7f5]">
          {!detail && <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">Select a conversation to view the thread.</div>}
          {detail && (
            <>
              <div className="h-14 shrink-0 flex items-center justify-between px-6 border-b border-border/60 bg-white">
                <div>
                  <div className="text-sm font-semibold">{detail.customer_name}</div>
                  <div className="text-xs text-muted-foreground">{detail.customer?.city} · {detail.customer?.preferred_language}</div>
                </div>
                {detail.mode === "HUMAN_ACTIVE" ? (
                  <button data-testid="return-ai-btn" onClick={() => takeover(true)} className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md bg-primary text-primary-foreground text-sm hover:bg-primary/90 transition-colors">
                    <Robot size={15} /> Return to AI
                  </button>
                ) : (
                  <button data-testid="takeover-btn" onClick={() => takeover(false)} className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md border border-input bg-white text-sm hover:bg-muted transition-colors">
                    <UserSwitch size={15} /> Take Over
                  </button>
                )}
              </div>

              <div ref={scrollBoxRef} className="flex-1 overflow-y-auto px-6 py-5 space-y-3">
                {detail.messages.map((m) => (
                  <div key={m.id} className={`flex ${m.direction === "outbound" ? "justify-end" : "justify-start"}`}>
                    <div className={`max-w-[70%] px-3.5 py-2 rounded-lg text-sm ${m.direction === "outbound" ? "bg-[hsl(var(--whatsapp))] text-white" : "bg-white border border-border/60"}`}>
                      <div className="whitespace-pre-wrap break-words">{m.body}</div>
                      <div className={`flex items-center gap-1 justify-end mt-1 text-[10px] ${m.direction === "outbound" ? "text-white/80" : "text-muted-foreground"}`}>
                        <span className="mr-1">{m.sender}</span>
                        {m.direction === "outbound" && (
                          <span data-testid={`msg-status-${m.id}`} className="inline-flex items-center">
                            <StatusTicks status={m.status} />
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
                <div ref={bottomRef} />
              </div>

              <div className="shrink-0 p-4 border-t border-border/60 bg-white flex items-center gap-2">
                <input
                  data-testid="message-input"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && send()}
                  placeholder={detail.mode === "HUMAN_ACTIVE" ? "Type a reply…" : "Take over to reply manually"}
                  disabled={detail.mode !== "HUMAN_ACTIVE" || sending}
                  className="flex-1 h-10 px-3 rounded-md border border-input text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:bg-muted/50"
                />
                <button data-testid="send-message-btn" onClick={send} disabled={detail.mode !== "HUMAN_ACTIVE" || sending} className="h-10 w-10 flex items-center justify-center rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50">{sending ? <span className="h-4 w-4 rounded-full border-2 border-primary-foreground/40 border-t-primary-foreground animate-spin" /> : <PaperPlaneRight size={16} weight="fill" />}</button>
              </div>
            </>
          )}
        </div>

        {/* Insights */}
        <div className="w-80 shrink-0 border-l border-border/60 overflow-y-auto p-5 space-y-5">
          <div>
            <div className="flex items-center gap-1.5 mb-2"><Sparkle size={15} className="text-[hsl(var(--whatsapp))]" /><h3 className="text-sm font-semibold font-display">AI Summary</h3></div>
            {detail?.ai_summary ? (
              <div className="text-sm space-y-1 border border-border/60 rounded-md p-3">
                {Object.entries(detail.ai_summary).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-2"><span className="text-muted-foreground capitalize">{k.replace(/_/g, " ")}</span><span className="font-medium text-right">{String(v)}</span></div>
                ))}
              </div>
            ) : <p className="text-xs text-muted-foreground border border-dashed rounded-md p-3">AI summary generates once the LLM is connected.</p>}
          </div>

          <div>
            <h3 className="text-sm font-semibold font-display mb-2">Customer Memory</h3>
            {detail?.customer ? (
              <div className="text-sm space-y-1 border border-border/60 rounded-md p-3">
                <Row k="Language" v={detail.customer.preferred_language} />
                <Row k="Previous orders" v={detail.customer.total_orders} />
                <Row k="City" v={detail.customer.city} />
              </div>
            ) : <p className="text-xs text-muted-foreground">No customer record.</p>}
          </div>

          <div>
            <div className="flex items-center gap-1.5 mb-2"><Robot size={15} /><h3 className="text-sm font-semibold font-display">AI Suggestion</h3></div>
            <p className="text-xs text-muted-foreground border border-dashed rounded-md p-3">AI suggested replies appear here during human takeover once the LLM is connected.</p>
          </div>
        </div>
      </div>
    </>
  );
}

const Row = ({ k, v }) => (
  <div className="flex justify-between"><span className="text-muted-foreground">{k}</span><span className="font-medium">{v ?? "—"}</span></div>
);

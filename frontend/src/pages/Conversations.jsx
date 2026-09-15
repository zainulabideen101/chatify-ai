import { useState, useEffect, useLayoutEffect, useRef } from "react";
import { PageHeader } from "@/components/Layout";
import { Pill, useFetch } from "@/components/bits";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { PaperPlaneRight, UserSwitch, Sparkle, Robot, Check, Checks, Phone, MapPin, Image as ImageIcon, Plus, Trash } from "@phosphor-icons/react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";

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

function formatPhone(phone) {
  const digits = String(phone || "").replace(/\D/g, "");
  if (!digits) return "Unknown number";
  const country = digits.slice(0, 2);
  const rest = digits.slice(2);
  return `+${country} ${rest.slice(0, 3)} ${rest.slice(3, 6)} ${rest.slice(6)}`.trim();
}

function initials(name, phone) {
  const value = String(name || phone || "?").trim();
  const parts = value.split(/\s+/).filter(Boolean);
  return (parts.length > 1 ? `${parts[0][0]}${parts[1][0]}` : value.slice(0, 2)).toUpperCase();
}

function Avatar({ name, phone, src, size = "h-10 w-10" }) {
  return src ? (
    <img data-testid="customer-avatar" src={src} alt={`${name || phone || "Customer"} profile`} className={`${size} rounded-full object-cover ring-2 ring-white shadow-sm`} />
  ) : (
    <div data-testid="customer-avatar" className={`${size} rounded-full bg-[hsl(var(--whatsapp))]/15 text-[hsl(var(--whatsapp-dark))] flex items-center justify-center text-xs font-semibold ring-2 ring-white shadow-sm`}>
      {initials(name, phone)}
    </div>
  );
}


export default function Conversations() {
  const { data: convos, loading, reload: reloadConvos } = useFetch("/conversations");
  const [activeId, setActiveId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [search, setSearch] = useState("");
  const [newChatOpen, setNewChatOpen] = useState(false);
  const [newPhone, setNewPhone] = useState("");
  const [creatingChat, setCreatingChat] = useState(false);
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

  // Position a newly selected thread at the latest message before it is painted.
  // Subsequent messages use a smooth scroll so live updates remain visible.
  useLayoutEffect(() => {
    const msgs = detail?.messages;
    if (!msgs || msgs.length === 0) return;
    const lastId = msgs[msgs.length - 1].id;
    const initialThreadLoad = lastMsgIdRef.current === null;
    lastMsgIdRef.current = lastId;
    if (initialThreadLoad) {
      if (scrollBoxRef.current) scrollBoxRef.current.scrollTop = scrollBoxRef.current.scrollHeight;
    } else {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [detail?.messages]);

  // Reset initial-load state when switching threads; the next detail render starts at bottom.
  useEffect(() => {
    lastMsgIdRef.current = null;
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
      if (!data.delivery.delivered) toast.info("Saved. Delivery is pending.");
    } catch { toast.error("Failed to send"); }
    finally { setSending(false); }
  };

  const createConversation = async (event) => {
    event.preventDefault();
    const phone = newPhone.trim();
    if (!phone || creatingChat) return;
    setCreatingChat(true);
    try {
      const { data } = await api.post("/conversations", { phone });
      const conversation = data.conversation;
      setNewChatOpen(false);
      setNewPhone("");
      setDetail(null);
      setActiveId(conversation.id);
      await reloadConvos();
      toast.success(data.existing ? "Conversation opened" : "New conversation ready");
    } catch (error) {
      const detail = error?.response?.data?.detail;
      toast.error(typeof detail === "string" ? detail : "Enter a valid Pakistani WhatsApp number");
    } finally {
      setCreatingChat(false);
    }
  };

  const deleteConversation = async () => {
    if (!activeId || !window.confirm("Delete this chat and all of its messages? Customer and order records will remain.")) return;
    try {
      await api.delete(`/conversations/${activeId}`);
      setDetail(null);
      setActiveId(null);
      await reloadConvos();
      toast.success("Chat deleted");
    } catch (error) {
      toast.error(error?.response?.data?.detail || "Failed to delete chat");
    }
  };

  const takeover = async (returnAi) => {
    const url = returnAi ? "return-to-ai" : "takeover";
    await api.post(`/conversations/${activeId}/${url}`);
    loadDetail(activeId);
    reloadConvos();
    toast.success(returnAi ? "Returned to AI" : "You have taken over");
  };

  if (loading) return <><PageHeader title="Conversations" /><div className="p-8">Loading…</div></>;

  const activePhone = detail?.customer_phone || detail?.customer?.phone;
  const activeName = detail?.customer?.whatsapp_name || detail?.customer?.name || detail?.whatsapp_name || detail?.customer_name;
  const activeAvatar = detail?.customer?.profile_picture_url || detail?.profile_picture_url;

  return (
    <>
      <PageHeader title="Conversations" subtitle="Unified WhatsApp inbox" />
      <Dialog open={newChatOpen} onOpenChange={setNewChatOpen}>
        <DialogContent data-testid="new-conversation-dialog">
          <DialogHeader>
            <DialogTitle>Start a new conversation</DialogTitle>
            <DialogDescription>Enter a Pakistani WhatsApp number, then use the composer to contact them.</DialogDescription>
          </DialogHeader>
          <form onSubmit={createConversation} className="space-y-4">
            <div className="space-y-2">
              <label htmlFor="new-chat-phone" className="text-sm font-medium">WhatsApp number</label>
              <input id="new-chat-phone" data-testid="new-conversation-phone-input" value={newPhone} onChange={(event) => setNewPhone(event.target.value)} placeholder="+92 300 0177889" required autoFocus className="w-full h-10 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
              <p data-testid="new-conversation-phone-help" className="text-xs text-muted-foreground">Use +92 followed by the 10-digit Pakistani mobile number.</p>
            </div>
            <DialogFooter>
              <button type="button" data-testid="new-conversation-cancel-button" onClick={() => setNewChatOpen(false)} className="h-9 px-3 rounded-md border border-input bg-background text-sm hover:bg-muted transition-colors">Cancel</button>
              <button type="submit" data-testid="new-conversation-submit-button" disabled={creatingChat} className="h-9 px-3 rounded-md bg-primary text-primary-foreground text-sm hover:bg-primary/90 transition-colors disabled:opacity-50">{creatingChat ? "Opening…" : "Open conversation"}</button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <div className="grid grid-cols-1 lg:grid-cols-12 h-[calc(100vh-4rem)] bg-background border-t border-border/60 overflow-hidden">
        {/* List */}
        <div className="lg:col-span-3 min-h-0 border-r border-border/60 flex flex-col bg-white overflow-hidden">
          <div className="px-5 pt-5 pb-4 border-b border-border/60 space-y-3">
            <div className="flex items-end justify-between gap-3">
              <div><p className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">Live inbox</p><h2 data-testid="conversations-list-heading" className="text-lg font-semibold tracking-tight">Conversations</h2></div>
              <div className="flex items-center gap-2">
                <span data-testid="conversation-count" className="text-xs font-mono text-muted-foreground">{convos.length} total</span>
                <button data-testid="new-conversation-button" onClick={() => setNewChatOpen(true)} aria-label="Start a new conversation" className="h-8 w-8 rounded-md bg-primary text-primary-foreground flex items-center justify-center hover:bg-primary/90 transition-colors">
                  <Plus size={16} weight="bold" />
                </button>
              </div>
            </div>
            <input data-testid="conversation-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search name or number" className="w-full h-9 px-3 rounded-md bg-muted/60 border border-transparent text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
          </div>
          <div className="flex-1 overflow-y-auto">
          {convos.length === 0 && <div className="p-6 text-sm text-muted-foreground">No conversations yet. They appear once customers message your WhatsApp.</div>}
          {convos.filter((c) => `${c.customer_name || ""} ${c.customer_phone || ""}`.toLowerCase().includes(search.toLowerCase())).map((c) => {
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
        </div>

        {/* Thread */}
        <div className="lg:col-span-6 min-h-0 flex flex-col min-w-0 bg-[#f4f5f7] relative">
          {!detail && <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">Select a conversation to view the thread.</div>}
          {detail && (
            <>
              <div data-testid="conversation-header" className="shrink-0 flex items-center justify-between gap-4 px-5 py-3 border-b border-border/60 bg-white shadow-[0_1px_0_rgba(0,0,0,0.02)]">
                <div className="flex items-center gap-3 min-w-0">
                  <Avatar name={activeName} phone={activePhone} src={activeAvatar} />
                  <div className="min-w-0">
                    <div data-testid="customer-whatsapp-name" className="text-sm font-semibold truncate">{activeName}</div>
                    <div data-testid="formatted-phone-number" className="flex items-center gap-1.5 text-[11px] font-mono tracking-wide text-muted-foreground"><Phone size={11} /> {formatPhone(activePhone)}</div>
                  </div>
                  <span data-testid="conversation-mode" className={`hidden sm:inline-flex items-center rounded-full px-2 py-1 text-[10px] font-semibold uppercase tracking-wide ${detail.mode === "HUMAN_ACTIVE" ? "bg-amber-100 text-amber-800" : "bg-emerald-100 text-emerald-800"}`}>{detail.mode === "HUMAN_ACTIVE" ? "Human" : "AI active"}</span>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {detail.mode === "HUMAN_ACTIVE" ? (
                    <button data-testid="return-ai-btn" onClick={() => takeover(true)} className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md bg-primary text-primary-foreground text-sm hover:bg-primary/90 transition-colors">
                      <Robot size={15} /> <span className="hidden sm:inline">Return to AI</span>
                    </button>
                  ) : (
                    <button data-testid="takeover-btn" onClick={() => takeover(false)} className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md border border-input bg-white text-sm hover:bg-muted transition-colors">
                      <UserSwitch size={15} /> <span className="hidden sm:inline">Take Over</span>
                    </button>
                  )}
                  <button data-testid="delete-conversation-button" onClick={deleteConversation} aria-label="Delete chat" className="h-9 w-9 rounded-md border border-red-200 text-red-600 flex items-center justify-center hover:bg-red-50 transition-colors">
                    <Trash size={16} />
                  </button>
                </div>
              </div>

              <div ref={scrollBoxRef} data-testid="message-scroll-container" className="flex-1 overflow-y-auto px-6 py-5 space-y-3">
                {detail.messages.map((m, index) => (
                  <div key={m.id} data-testid={`message-item-${m.id}`} className={`flex ${m.direction === "outbound" ? "justify-end" : "justify-start"} ${index > 0 && detail.messages[index - 1].direction === m.direction ? "-mt-1" : ""}`}>
                    <div className={`max-w-[78%] px-3.5 py-2.5 rounded-2xl text-sm shadow-sm ${m.direction === "outbound" ? "bg-emerald-100 text-emerald-950 rounded-br-md" : "bg-white border border-border/60 rounded-bl-md"}`}>
                      {m.message_type === "image" && <div data-testid="payment-attachment-preview" className="mb-2 flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-2 text-xs text-amber-900"><ImageIcon size={15} /> Payment screenshot received</div>}
                      <div className="whitespace-pre-wrap break-words leading-relaxed">{m.body}</div>
                      <div className="flex items-center gap-1.5 justify-end mt-1.5 text-[10px] text-muted-foreground">
                        <span>{m.sender}</span>
                        <span>{m.created_at ? new Date(m.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}</span>
                        {m.direction === "outbound" && <span data-testid={`msg-status-${m.id}`} className="inline-flex items-center"><StatusTicks status={m.status} /></span>}
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
        <div className="lg:col-span-3 min-h-0 border-l border-border/60 flex flex-col bg-white overflow-y-auto p-5 space-y-5">
          <div data-testid="customer-profile-card" className="rounded-xl border border-border/60 bg-white p-4">
            <div className="flex items-center gap-3">
              <Avatar name={activeName} phone={activePhone} src={activeAvatar} size="h-12 w-12" />
              <div className="min-w-0"><h3 className="text-sm font-semibold truncate">{activeName || "Customer"}</h3><p className="text-[11px] font-mono text-muted-foreground mt-0.5">{formatPhone(activePhone)}</p></div>
            </div>
            <div className="grid grid-cols-2 gap-2 mt-4 text-xs"><div className="rounded-md bg-muted/60 p-2"><Phone size={13} className="mb-1 text-muted-foreground" /><span className="text-muted-foreground">Phone</span><strong data-testid="customer-phone-detail" className="block mt-0.5 truncate">{activePhone || "—"}</strong></div><div className="rounded-md bg-muted/60 p-2"><MapPin size={13} className="mb-1 text-muted-foreground" /><span className="text-muted-foreground">City</span><strong className="block mt-0.5 truncate">{detail?.customer?.city || "—"}</strong></div></div>
          </div>

          <div>
            <div className="flex items-center gap-1.5 mb-2"><Sparkle size={15} className="text-[hsl(var(--whatsapp))]" /><h3 className="text-sm font-semibold font-display">AI Summary</h3></div>
            {detail?.ai_summary ? <div className="text-sm space-y-1 border border-border/60 rounded-md p-3">{Object.entries(detail.ai_summary).map(([k, v]) => <div key={k} className="flex justify-between gap-2"><span className="text-muted-foreground capitalize">{k.replace(/_/g, " ")}</span><span className="font-medium text-right">{String(v)}</span></div>)}</div> : <p className="text-xs text-muted-foreground border border-dashed rounded-md p-3">AI summary generates once the LLM is connected.</p>}
          </div>

          <div>
            <h3 className="text-sm font-semibold font-display mb-2">Customer Memory</h3>
            {detail?.customer ? <div className="text-sm space-y-1 border border-border/60 rounded-md p-3"><Row k="Language" v={detail.customer.preferred_language} /><Row k="Previous orders" v={detail.customer.total_orders} /><Row k="City" v={detail.customer.city} /></div> : <p className="text-xs text-muted-foreground">No customer record.</p>}
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

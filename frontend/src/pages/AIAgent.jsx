import { useState, useRef, useEffect } from "react";
import { PageHeader } from "@/components/Layout";
import { Section, useFetch } from "@/components/bits";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Plus, Trash, Robot, PaperPlaneRight, ArrowClockwise, Sparkle } from "@phosphor-icons/react";

const TOGGLES = [
  ["cross_selling", "Cross-selling"],
  ["upselling", "Upselling"],
  ["abandoned_recovery", "Abandoned-order recovery"],
  ["customer_memory", "Customer memory"],
  ["media_sending", "Media sending"],
  ["ai_order_creation", "AI order creation"],
  ["human_escalation", "Human escalation"],
  ["cod_protection", "COD protection"],
];
const MODES = [["SUPPORT_ONLY", "Support only"], ["HELPFUL_SALES", "Helpful sales"], ["AGGRESSIVE_SALES", "Aggressive sales"]];

export default function AIAgent() {
  const { data: settings, setData: setSettings } = useFetch("/ai-settings");
  const { data: rules, reload: reloadRules } = useFetch("/business-rules");
  const { data: tools } = useFetch("/ai/tools");
  const [rule, setRule] = useState("");

  const patch = async (updates) => {
    const { data } = await api.put("/ai-settings", updates);
    setSettings(data);
    toast.success("Saved");
  };
  const addRule = async () => {
    if (!rule.trim()) return;
    await api.post("/business-rules", { instruction: rule, category: "general" });
    setRule(""); reloadRules(); toast.success("Rule added");
  };
  const delRule = async (id) => { await api.delete(`/business-rules/${id}`); reloadRules(); };

  return (
    <>
      <PageHeader title="AI Agent" subtitle="Sales behaviour, rules & tool system" />
      <Section>
        {settings && (
          <div className="grid lg:grid-cols-2 gap-6">
            <div className="border border-border/60 rounded-lg bg-card p-5 space-y-4">
              <h3 className="font-display text-sm font-semibold">Sales mode</h3>
              <div className="grid grid-cols-3 gap-2">
                {MODES.map(([v, l]) => (
                  <button key={v} data-testid={`mode-${v}`} onClick={() => patch({ sales_mode: v })} className={`text-xs px-2 py-2.5 rounded-md border transition-colors ${settings.sales_mode === v ? "bg-primary text-primary-foreground border-primary" : "border-input hover:bg-muted"}`}>{l}</button>
                ))}
              </div>
              <div className="border-t border-border/60 pt-4 space-y-3">
                {TOGGLES.map(([k, label]) => (
                  <label key={k} className="flex items-center justify-between text-sm">
                    <span>{label}</span>
                    <button data-testid={`toggle-${k}`} onClick={() => patch({ [k]: !settings[k] })} className={`h-5 w-9 rounded-full transition-colors relative ${settings[k] ? "bg-[hsl(var(--whatsapp))]" : "bg-muted"}`}>
                      <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${settings[k] ? "translate-x-4" : "translate-x-0.5"}`} />
                    </button>
                  </label>
                ))}
              </div>
              <div className="border-t border-border/60 pt-4 flex items-center justify-between text-sm">
                <span>Max discount (%)</span>
                <input data-testid="max-discount" type="number" defaultValue={settings.max_discount_percent} onBlur={(e) => patch({ max_discount_percent: Number(e.target.value) })} className="w-20 h-8 px-2 rounded-md border border-input text-sm tnum" />
              </div>
            </div>

            <div className="space-y-6">
              <div className="border border-border/60 rounded-lg bg-card p-5 space-y-3">
                <h3 className="font-display text-sm font-semibold">Teach your AI</h3>
                <div className="flex gap-2">
                  <input data-testid="rule-input" value={rule} onChange={(e) => setRule(e.target.value)} placeholder='e.g. "For Lahore delivery, say 1-2 working days"' className="flex-1 h-9 px-3 rounded-md border border-input text-sm" />
                  <button data-testid="add-rule-btn" onClick={addRule} className="h-9 w-9 flex items-center justify-center rounded-md bg-primary text-primary-foreground"><Plus size={15} /></button>
                </div>
                <div className="space-y-2">
                  {(rules || []).map((r) => (
                    <div key={r.id} className="flex items-start justify-between gap-2 text-sm border border-border/60 rounded-md p-2.5">
                      <span>{r.instruction}</span>
                      <button data-testid={`del-rule-${r.id}`} onClick={() => delRule(r.id)} className="text-muted-foreground hover:text-destructive"><Trash size={14} /></button>
                    </div>
                  ))}
                </div>
              </div>

              <div className="border border-border/60 rounded-lg bg-card p-5">
                <div className="flex items-center gap-2 mb-3">
                  <Robot size={16} />
                  <h3 className="font-display text-sm font-semibold">Controlled Tool System</h3>
                  <span className={`ml-auto text-[11px] px-2 py-0.5 rounded-md ${tools?.llm_connected ? "bg-green-100 text-green-800" : "bg-amber-100 text-amber-800"}`}>
                    {tools?.llm_connected ? "LLM connected" : "LLM not connected"}
                  </span>
                </div>
                <p className="text-xs text-muted-foreground mb-3">The LLM requests a tool; the backend validates & executes. {tools?.tools?.length || 0} tools registered.</p>
                <div className="grid grid-cols-2 gap-1.5 max-h-52 overflow-y-auto">
                  {(tools?.tools || []).map((t) => (
                    <div key={t.name} className="text-[11px] border border-border/60 rounded px-2 py-1.5 flex items-center justify-between">
                      <span className="mono truncate">{t.name}</span>
                      {t.side_effect && <span className="text-amber-600" title="side effect">●</span>}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}
        <Playground />
      </Section>
    </>
  );
}

function Playground() {
  const SESSION = "playground-main";
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef(null);

  useEffect(() => {
    api.get(`/ai/playground/${SESSION}`).then((r) => setMessages(r.data.messages || []));
  }, []);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, busy]);

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }]);
    setBusy(true);
    try {
      const { data } = await api.post(`/ai/playground/${SESSION}`, { message: text });
      setMessages((m) => [...m, { role: "assistant", content: data.reply, tool_trace: data.tool_trace }]);
    } catch (e) {
      toast.error(e.response?.data?.detail || "AI error");
    } finally { setBusy(false); }
  };
  const reset = async () => {
    await api.post(`/ai/playground/${SESSION}/reset`);
    setMessages([]);
  };

  const examples = ["bhai black hoodie medium mein hai?", "real pic bhejo", "return policy kya hai?", "iske sath kuch aur suggest karo"];

  return (
    <div className="border border-border/60 rounded-lg bg-card overflow-hidden" data-testid="ai-playground">
      <div className="flex items-center justify-between px-5 py-3 border-b border-border/60">
        <div className="flex items-center gap-2">
          <Sparkle size={16} className="text-[hsl(var(--whatsapp))]" weight="fill" />
          <h3 className="font-display text-sm font-semibold">Test Playground</h3>
          <span className="text-[11px] text-muted-foreground">GPT-5.4 · live catalog · testing only</span>
        </div>
        <button data-testid="playground-reset" onClick={reset} className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"><ArrowClockwise size={13} /> Reset</button>
      </div>

      <div className="h-80 overflow-y-auto px-5 py-4 space-y-3 bg-[#f7f7f5]">
        {messages.length === 0 && (
          <div className="text-center text-xs text-muted-foreground pt-10">
            Chat with your AI employee as if you were a customer on WhatsApp.
            <div className="flex flex-wrap gap-1.5 justify-center mt-3">
              {examples.map((e) => <button key={e} onClick={() => setInput(e)} className="text-[11px] bg-white border border-border/60 rounded-full px-2.5 py-1 hover:bg-muted">{e}</button>)}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[75%] px-3.5 py-2 rounded-lg text-sm whitespace-pre-wrap ${m.role === "user" ? "bg-[hsl(var(--whatsapp))] text-white" : "bg-white border border-border/60"}`}>
              {m.content}
              {m.tool_trace?.length > 0 && (
                <div className="mt-2 pt-2 border-t border-border/40 flex flex-wrap gap-1">
                  {m.tool_trace.map((t, j) => <span key={j} className="text-[10px] mono bg-muted rounded px-1.5 py-0.5 text-muted-foreground">{t.tool}</span>)}
                </div>
              )}
            </div>
          </div>
        ))}
        {busy && <div className="text-xs text-muted-foreground">AI is thinking…</div>}
        <div ref={endRef} />
      </div>

      <div className="p-3 border-t border-border/60 flex items-center gap-2">
        <input data-testid="playground-input" value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} placeholder="Type as a customer… (Roman Urdu / English)" className="flex-1 h-10 px-3 rounded-md border border-input text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
        <button data-testid="playground-send" onClick={send} disabled={busy} className="h-10 w-10 flex items-center justify-center rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"><PaperPlaneRight size={16} weight="fill" /></button>
      </div>
    </div>
  );
}

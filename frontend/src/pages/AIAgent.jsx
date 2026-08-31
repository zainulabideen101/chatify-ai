import { useState } from "react";
import { PageHeader } from "@/components/Layout";
import { Section, useFetch } from "@/components/bits";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Plus, Trash, Robot } from "@phosphor-icons/react";

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
      </Section>
    </>
  );
}

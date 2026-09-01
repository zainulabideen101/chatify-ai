import { useState } from "react";
import { PageHeader } from "@/components/Layout";
import { Section, EmptyState, useFetch } from "@/components/bits";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Plus, Trash, Warning, CheckCircle } from "@phosphor-icons/react";

const blankStep = () => ({ delay_minutes: 60, template_name: "abandoned_order", language: "en", discount_percent: 0, enabled: true });

export default function Recovery() {
  const { data: settings, loading, setData: setSettings } = useFetch("/recovery/settings");
  const { data: abandoned, reload } = useFetch("/abandoned-orders");
  const [saving, setSaving] = useState(false);

  const patch = (key, value) => setSettings({ ...settings, [key]: value });
  const patchStep = (index, key, value) => setSettings({ ...settings, steps: settings.steps.map((step, i) => i === index ? { ...step, [key]: value } : step) });
  const addStep = () => {
    if ((settings.steps || []).length >= 5) return toast.error("You can add up to five follow-ups");
    patch("steps", [...(settings.steps || []), blankStep()]);
  };
  const removeStep = (index) => patch("steps", settings.steps.filter((_, i) => i !== index));
  const save = async () => {
    if (!settings.steps?.length) return toast.error("Add at least one approved template step");
    setSaving(true);
    try {
      const { data } = await api.put("/recovery/settings", settings);
      setSettings(data); toast.success("Recovery settings saved"); reload();
    } catch (e) { toast.error(e.response?.data?.detail || "Could not save recovery settings"); }
    finally { setSaving(false); }
  };

  if (loading || !settings) return <><PageHeader title="Abandoned cart recovery" /><Section>Loading…</Section></>;
  const active = (abandoned || []).filter((item) => item.status === "abandoned");
  return (
    <>
      <PageHeader title="Abandoned cart recovery" subtitle="Seller-controlled WhatsApp follow-ups" actions={<button data-testid="save-recovery-btn" onClick={save} disabled={saving} className="h-9 px-3 rounded-md bg-primary text-primary-foreground text-sm hover:bg-primary/90 disabled:opacity-60">{saving ? "Saving…" : "Save settings"}</button>} />
      <Section>
        <div className="border border-border/60 rounded-lg bg-card p-5 space-y-5">
          <div className="flex items-start gap-3"><div className="flex-1"><h2 className="font-display text-sm font-semibold">Automation</h2><p className="text-xs text-muted-foreground mt-1">Follow up only after a customer stops replying. Customers receive normal WhatsApp messages and never see internal AI or human labels.</p></div><button data-testid="recovery-enabled" onClick={() => patch("enabled", !settings.enabled)} className={`h-5 w-9 rounded-full transition-colors relative ${settings.enabled ? "bg-[hsl(var(--whatsapp))]" : "bg-muted"}`}><span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${settings.enabled ? "translate-x-4" : "translate-x-0.5"}`} /></button></div>
          <label className="block space-y-1.5 max-w-xs"><span className="text-xs font-medium">Start after no customer reply (minutes)</span><input data-testid="recovery-trigger-delay" type="number" min="1" max="10080" value={settings.trigger_delay_minutes} onChange={(e) => patch("trigger_delay_minutes", Number(e.target.value))} className="w-full h-10 px-3 rounded-md border border-input text-sm tnum" /><span className="text-xs text-muted-foreground">This is the inactivity trigger for the first follow-up.</span></label>
          <div className="border-t border-border/60 pt-5 space-y-3"><div className="flex items-center justify-between"><div><h3 className="font-display text-sm font-semibold">Follow-up sequence</h3><p className="text-xs text-muted-foreground mt-1">Each step uses a Meta-approved abandoned_order template.</p></div><button data-testid="add-recovery-step" onClick={addStep} className="inline-flex items-center gap-1 h-8 px-2.5 rounded-md border border-input text-xs hover:bg-muted"><Plus size={14} /> Add step</button></div>
            {(settings.steps || []).map((step, index) => <div key={`${index}-${step.template_name}`} className="border border-border/60 rounded-md p-4 grid md:grid-cols-[1fr_1fr_120px_32px] gap-3 items-end"><label className="block space-y-1"><span className="text-[11px] text-muted-foreground">{index === 0 ? "First step delay" : "Wait after previous (minutes)"}</span><input data-testid={`recovery-step-delay-${index}`} type="number" min="0" max="10080" value={step.delay_minutes} onChange={(e) => patchStep(index, "delay_minutes", Number(e.target.value))} className="w-full h-9 px-2 rounded-md border border-input text-sm tnum" /></label><label className="block space-y-1"><span className="text-[11px] text-muted-foreground">Approved template name</span><input data-testid={`recovery-step-template-${index}`} value={step.template_name} onChange={(e) => patchStep(index, "template_name", e.target.value)} className="w-full h-9 px-2 rounded-md border border-input text-sm mono" /></label><label className="block space-y-1"><span className="text-[11px] text-muted-foreground">Discount %</span><input data-testid={`recovery-step-discount-${index}`} type="number" min="0" max="100" value={step.discount_percent} onChange={(e) => patchStep(index, "discount_percent", Number(e.target.value))} className="w-full h-9 px-2 rounded-md border border-input text-sm tnum" /></label><button data-testid={`remove-recovery-step-${index}`} onClick={() => removeStep(index)} disabled={settings.steps.length <= 1} className="h-9 w-8 flex items-center justify-center text-muted-foreground hover:text-destructive disabled:opacity-30"><Trash size={15} /></button></div>)}
          </div>
          <div className="border-t border-border/60 pt-5"><h3 className="font-display text-sm font-semibold mb-3">Stop conditions</h3><div className="grid md:grid-cols-3 gap-3">{[["stop_on_customer_reply", "Customer replies"], ["stop_on_order_recovered", "Order is recovered"], ["stop_when_seller_pauses", "Seller pauses automation"]].map(([key, label]) => <label key={key} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={settings[key]} onChange={(e) => patch(key, e.target.checked)} />{label}</label>)}</div></div>
        </div>

        <div className="border border-border/60 rounded-lg bg-card p-5"><div className="flex items-center gap-2 mb-4"><h2 className="font-display text-sm font-semibold">Open recovery opportunities</h2><span className="text-xs text-muted-foreground">{active.length} active</span></div>{!active.length ? <EmptyState title="No active abandoned carts" hint="New opportunities appear when customers leave an order unfinished." /> : <div className="space-y-2">{active.map((item) => <div key={item.id} data-testid={`recovery-item-${item.id}`} className="flex flex-wrap items-center justify-between gap-3 border border-border/60 rounded-md p-3"><div><p className="text-sm font-medium">{item.customer_name || item.customer_phone || "Customer"}</p><p className="text-xs text-muted-foreground">{item.product || "Cart"} · {item.variant || ""} · Rs. {Number(item.order_value || 0).toLocaleString("en-PK")}</p></div><span className="inline-flex items-center gap-1 text-xs text-muted-foreground">{item.recovery_status === "sent" ? <><CheckCircle size={14} className="text-green-600" /> Follow-up sent</> : <><Warning size={14} className="text-amber-600" /> Waiting for schedule</>}</span></div>)}</div>}</div>

        <p className="text-xs text-muted-foreground border border-border/60 rounded-lg p-4">Create and submit a Meta-approved template with use case <span className="mono">abandoned_order</span> in Templates before enabling live sends. The scheduler never sends an unapproved template and never fabricates a delivery result.</p>
      </Section>
    </>
  );
}

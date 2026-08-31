import { useState } from "react";
import { PageHeader } from "@/components/Layout";
import { Pill, Section, EmptyState, useFetch } from "@/components/bits";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Plus, Trash } from "@phosphor-icons/react";

const USE_CASES = ["order_confirmation", "order_shipped", "order_out_for_delivery", "order_delivered", "address_clarification", "abandoned_order", "payment_reminder"];

export default function Templates() {
  const { data, loading, reload } = useFetch("/templates");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", use_case: "order_confirmation", language: "en", category: "UTILITY", body: "" });

  const save = async () => {
    if (!form.name || !form.body) return toast.error("Name and body required");
    await api.post("/templates", { ...form, variables: (form.body.match(/{{\d+}}/g) || []).map((v) => v) });
    toast.success("Template saved"); setOpen(false); reload();
  };
  const del = async (id) => { await api.delete(`/templates/${id}`); toast.success("Deleted"); reload(); };

  return (
    <>
      <PageHeader title="Templates" subtitle="Meta-approved message templates" actions={
        <button data-testid="add-template-btn" onClick={() => setOpen(true)} className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md bg-primary text-primary-foreground text-sm hover:bg-primary/90"><Plus size={15} /> New template</button>
      } />
      <Section>
        {loading ? "Loading…" : !data.length ? <EmptyState title="No templates" /> : (
          <div className="grid md:grid-cols-2 gap-4">
            {data.map((t) => (
              <div key={t.id} data-testid={`template-${t.id}`} className="border border-border/60 rounded-lg bg-card p-5 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-medium text-sm mono">{t.name}</span>
                  <div className="flex items-center gap-2">
                    <Pill tone={t.approval_status}>{t.approval_status}</Pill>
                    <button data-testid={`del-template-${t.id}`} onClick={() => del(t.id)} className="text-muted-foreground hover:text-destructive"><Trash size={14} /></button>
                  </div>
                </div>
                <div className="text-xs text-muted-foreground">{t.use_case} · {t.language.toUpperCase()} · {t.category}</div>
                <p className="text-sm bg-muted/40 rounded-md p-3">{t.body}</p>
              </div>
            ))}
          </div>
        )}
      </Section>
      {open && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={() => setOpen(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-md p-6 space-y-4" onClick={(e) => e.stopPropagation()}>
            <h3 className="font-display text-lg font-semibold">New template</h3>
            <label className="block space-y-1.5"><span className="text-xs font-medium">Name</span><input data-testid="tpl-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="w-full h-10 px-3 rounded-md border border-input text-sm mono" /></label>
            <label className="block space-y-1.5"><span className="text-xs font-medium">Use case</span>
              <select data-testid="tpl-usecase" value={form.use_case} onChange={(e) => setForm({ ...form, use_case: e.target.value })} className="w-full h-10 px-3 rounded-md border border-input text-sm bg-card">
                {USE_CASES.map((u) => <option key={u} value={u}>{u}</option>)}
              </select>
            </label>
            <label className="block space-y-1.5"><span className="text-xs font-medium">Body (use {"{{1}}"} for variables)</span><textarea data-testid="tpl-body" value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} rows={3} className="w-full px-3 py-2 rounded-md border border-input text-sm" /></label>
            <div className="flex gap-2 justify-end"><button onClick={() => setOpen(false)} className="h-9 px-3 rounded-md border border-input text-sm">Cancel</button><button data-testid="save-template-btn" onClick={save} className="h-9 px-3 rounded-md bg-primary text-primary-foreground text-sm">Save</button></div>
          </div>
        </div>
      )}
    </>
  );
}

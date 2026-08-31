import { useState } from "react";
import { PageHeader } from "@/components/Layout";
import { Section, EmptyState, useFetch } from "@/components/bits";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Plus, Trash, BookOpen } from "@phosphor-icons/react";

const CATS = ["shipping", "returns", "exchange", "warranty", "faq", "size_guide", "policy", "instruction"];

export default function KnowledgeBase() {
  const { data, loading, reload } = useFetch("/knowledge-base");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ title: "", category: "faq", content: "" });

  const save = async () => {
    if (!form.title || !form.content) return toast.error("Title and content required");
    await api.post("/knowledge-base", form);
    toast.success("Saved"); setOpen(false); setForm({ title: "", category: "faq", content: "" }); reload();
  };
  const del = async (id) => { await api.delete(`/knowledge-base/${id}`); reload(); };

  return (
    <>
      <PageHeader title="Knowledge Base" subtitle="Policies & FAQs the AI retrieves before answering" actions={
        <button data-testid="add-kb-btn" onClick={() => setOpen(true)} className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md bg-primary text-primary-foreground text-sm hover:bg-primary/90"><Plus size={15} /> Add document</button>
      } />
      <Section>
        <div className="text-xs bg-muted/40 border border-border/60 rounded-md p-3 mb-2 flex items-center gap-2">
          <BookOpen size={15} /> The AI uses retrieval (RAG) over these documents — it never places the whole knowledge base into the prompt or invents policy answers.
        </div>
        {loading ? "Loading…" : !data.length ? <EmptyState title="No documents" hint="Add shipping, return and warranty policies, size guides and FAQs." /> : (
          <div className="space-y-3">
            {data.map((d) => (
              <div key={d.id} data-testid={`kb-${d.id}`} className="border border-border/60 rounded-lg bg-card p-5">
                <div className="flex items-center justify-between mb-1.5">
                  <div className="flex items-center gap-2"><span className="font-medium text-sm">{d.title}</span><span className="text-[10px] uppercase bg-secondary rounded px-1.5 py-0.5">{d.category}</span></div>
                  <button data-testid={`del-kb-${d.id}`} onClick={() => del(d.id)} className="text-muted-foreground hover:text-destructive"><Trash size={15} /></button>
                </div>
                <p className="text-sm text-muted-foreground">{d.content}</p>
              </div>
            ))}
          </div>
        )}
      </Section>
      {open && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={() => setOpen(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-md p-6 space-y-4" onClick={(e) => e.stopPropagation()}>
            <h3 className="font-display text-lg font-semibold">Add document</h3>
            <label className="block space-y-1.5"><span className="text-xs font-medium">Title</span><input data-testid="kb-title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} className="w-full h-10 px-3 rounded-md border border-input text-sm" /></label>
            <label className="block space-y-1.5"><span className="text-xs font-medium">Category</span>
              <select data-testid="kb-category" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} className="w-full h-10 px-3 rounded-md border border-input text-sm bg-card">{CATS.map((c) => <option key={c} value={c}>{c}</option>)}</select>
            </label>
            <label className="block space-y-1.5"><span className="text-xs font-medium">Content</span><textarea data-testid="kb-content" value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} rows={4} className="w-full px-3 py-2 rounded-md border border-input text-sm" /></label>
            <div className="flex gap-2 justify-end"><button onClick={() => setOpen(false)} className="h-9 px-3 rounded-md border border-input text-sm">Cancel</button><button data-testid="save-kb-btn" onClick={save} className="h-9 px-3 rounded-md bg-primary text-primary-foreground text-sm">Save</button></div>
          </div>
        </div>
      )}
    </>
  );
}

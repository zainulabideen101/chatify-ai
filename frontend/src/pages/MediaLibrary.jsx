import { useState } from "react";
import { PageHeader } from "@/components/Layout";
import { Section, EmptyState, useFetch } from "@/components/bits";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Plus, VideoCamera, Image as ImageIcon, Trash } from "@phosphor-icons/react";

const TAGS = ["real_photo", "customer_photo", "wearing_photo", "wearing_video", "product_video", "fabric_closeup", "color_reference", "packaging", "size_reference", "how_to_use"];

export default function MediaLibrary() {
  const { data, loading, reload } = useFetch("/media");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ filename: "", media_type: "image", url: "", tags: [], description: "" });

  const del = async (id) => { await api.delete(`/media/${id}`); toast.success("Deleted"); reload(); };

  const save = async () => {
    if (!form.url || !form.filename) return toast.error("Filename and URL required");
    await api.post("/media", form);
    toast.success("Media added");
    setOpen(false);
    setForm({ filename: "", media_type: "image", url: "", tags: [], description: "" });
    reload();
  };
  const toggleTag = (t) => setForm((f) => ({ ...f, tags: f.tags.includes(t) ? f.tags.filter((x) => x !== t) : [...f.tags, t] }));

  return (
    <>
      <PageHeader title="Media Library" subtitle="Real product photos & videos the AI can send" actions={
        <button data-testid="add-media-btn" onClick={() => setOpen(true)} className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md bg-primary text-primary-foreground text-sm hover:bg-primary/90 transition-colors"><Plus size={15} /> Add media</button>
      } />
      <Section>
        {loading ? "Loading…" : !data.length ? <EmptyState title="No media yet" hint="Upload real photos and videos and tag them so the AI sends the right asset." /> : (
          <div className="grid sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
            {data.map((m) => (
              <div key={m.id} data-testid={`media-${m.id}`} className="border border-border/60 rounded-lg bg-card overflow-hidden">
                <div className="relative h-36 bg-muted">
                  <img src={m.url} alt={m.filename} className="h-full w-full object-cover" />
                  <span className="absolute top-2 left-2 bg-white/90 rounded px-1.5 py-0.5 text-[10px] flex items-center gap-1">
                    {m.media_type === "video" ? <VideoCamera size={11} /> : <ImageIcon size={11} />}{m.media_type}
                  </span>
                </div>
                <div className="p-3 space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-xs font-medium truncate">{m.filename}</div>
                    <button data-testid={`del-media-${m.id}`} onClick={() => del(m.id)} className="text-muted-foreground hover:text-destructive shrink-0"><Trash size={13} /></button>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {m.tags.map((t) => <span key={t} className="text-[10px] bg-secondary rounded px-1.5 py-0.5">{t}</span>)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      {open && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={() => setOpen(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-md p-6 space-y-4" onClick={(e) => e.stopPropagation()}>
            <h3 className="font-display text-lg font-semibold">Add media</h3>
            <Input label="Filename" value={form.filename} onChange={(e) => setForm({ ...form, filename: e.target.value })} testid="media-filename" />
            <Input label="Media URL" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} testid="media-url" placeholder="https://…" />
            <div className="space-y-1.5">
              <span className="text-xs font-medium">Type</span>
              <select data-testid="media-type" value={form.media_type} onChange={(e) => setForm({ ...form, media_type: e.target.value })} className="w-full h-10 px-3 rounded-md border border-input text-sm bg-card">
                <option value="image">Image</option><option value="video">Video</option>
              </select>
            </div>
            <div className="space-y-1.5">
              <span className="text-xs font-medium">Tags</span>
              <div className="flex flex-wrap gap-1.5">
                {TAGS.map((t) => <button key={t} type="button" onClick={() => toggleTag(t)} className={`text-[11px] px-2 py-1 rounded-md border transition-colors ${form.tags.includes(t) ? "bg-primary text-primary-foreground border-primary" : "border-input hover:bg-muted"}`}>{t}</button>)}
              </div>
            </div>
            <div className="flex gap-2 justify-end pt-2">
              <button onClick={() => setOpen(false)} className="h-9 px-3 rounded-md border border-input text-sm hover:bg-muted">Cancel</button>
              <button data-testid="save-media-btn" onClick={save} className="h-9 px-3 rounded-md bg-primary text-primary-foreground text-sm hover:bg-primary/90">Save</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function Input({ label, testid, ...props }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium">{label}</span>
      <input data-testid={testid} className="w-full h-10 px-3 rounded-md border border-input text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" {...props} />
    </label>
  );
}

import { useState } from "react";
import { PageHeader } from "@/components/Layout";
import { Pill, Section, EmptyState, useFetch } from "@/components/bits";
import { api, rs } from "@/lib/api";

export default function Customers() {
  const { data, loading } = useFetch("/customers");
  const [selected, setSelected] = useState(null);

  const open = (id) => api.get(`/customers/${id}`).then((r) => setSelected(r.data));

  return (
    <>
      <PageHeader title="Customers" subtitle="Commerce memory & COD risk" />
      <Section>
        {loading ? "Loading…" : !data.length ? (
          <EmptyState title="No customers yet" />
        ) : (
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
            {data.map((c) => (
              <button key={c.id} data-testid={`customer-${c.id}`} onClick={() => open(c.id)} className="text-left border border-border/60 rounded-lg bg-card p-5 hover:border-primary/40 transition-colors">
                <div className="flex items-center gap-3">
                  <div className="h-10 w-10 rounded-full bg-secondary flex items-center justify-center font-semibold">{c.name.slice(0, 1)}</div>
                  <div><div className="text-sm font-medium">{c.name}</div><div className="text-xs text-muted-foreground">{c.phone}</div></div>
                </div>
                <div className="grid grid-cols-3 gap-2 mt-4 text-center">
                  <Mini k="Orders" v={c.total_orders} />
                  <Mini k="Delivered" v={c.delivered_orders} />
                  <Mini k="Language" v={c.preferred_language} />
                </div>
              </button>
            ))}
          </div>
        )}
      </Section>

      {selected && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={() => setSelected(null)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-md p-6 space-y-4" onClick={(e) => e.stopPropagation()} data-testid="customer-detail">
            <div className="flex items-center justify-between">
              <h3 className="font-display text-lg font-semibold">{selected.name}</h3>
              <Pill tone={selected.cod_risk.level}>COD {selected.cod_risk.level}</Pill>
            </div>
            <div className="text-sm space-y-1.5">
              <Row k="Phone" v={selected.phone} />
              <Row k="City" v={selected.city} />
              <Row k="Preferred language" v={selected.preferred_language} />
              <Row k="Preferred size" v={selected.memory?.preferred_size} />
              <Row k="Previous purchases" v={selected.total_orders} />
              <Row k="Last purchase" v={selected.memory?.last_purchase} />
            </div>
            <div className="border-t border-border/60 pt-3">
              <div className="text-xs uppercase text-muted-foreground mb-1">COD risk reasons</div>
              <ul className="text-sm list-disc list-inside text-muted-foreground">
                {selected.cod_risk.reasons.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            </div>
            <div className="border-t border-border/60 pt-3">
              <div className="text-xs uppercase text-muted-foreground mb-2">Orders</div>
              {selected.orders.map((o) => (
                <div key={o.id} className="flex justify-between text-sm py-1"><span>#{o.order_number}</span><span className="tnum">{rs(o.total)}</span></div>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

const Mini = ({ k, v }) => (
  <div className="bg-muted/40 rounded-md py-2"><div className="text-sm font-semibold">{v ?? "—"}</div><div className="text-[10px] text-muted-foreground">{k}</div></div>
);
const Row = ({ k, v }) => (
  <div className="flex justify-between"><span className="text-muted-foreground">{k}</span><span className="font-medium">{v ?? "—"}</span></div>
);

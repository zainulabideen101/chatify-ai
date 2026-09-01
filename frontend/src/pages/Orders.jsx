import { PageHeader } from "@/components/Layout";
import { Pill, Section, EmptyState, useFetch } from "@/components/bits";
import { rs } from "@/lib/api";

export default function Orders() {
  const { data, loading } = useFetch("/orders");
  return (
    <>
      <PageHeader title="Orders" subtitle="AI & human generated orders" />
      <Section>
        {loading ? "Loading…" : !data.length ? (
          <EmptyState title="No orders yet" hint="Orders created by the AI on WhatsApp and synced from Shopify will appear here." />
        ) : (
          <div className="border border-border/60 rounded-lg bg-card overflow-hidden">
            <table className="w-full text-sm">
              <thead className="text-[11px] uppercase text-muted-foreground text-left bg-muted/40">
                <tr>
                  <th className="px-4 py-2.5 font-medium">Order</th>
                  <th className="font-medium">Items</th>
                  <th className="font-medium">Total</th>
                  <th className="font-medium">Payment</th>
                  <th className="font-medium">Payment verification</th>
                  <th className="font-medium">Attribution</th>
                  <th className="px-4 font-medium">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {data.map((o) => (
                  <tr key={o.id} data-testid={`order-${o.id}`} className="hover:bg-muted/40">
                    <td className="px-4 py-3 font-medium tnum">#{o.order_number}</td>
                    <td className="text-muted-foreground">{(o.items || []).map((i) => `${i.title} ×${i.qty}`).join(", ")}</td>
                    <td className="tnum font-medium">{rs(o.total)}</td>
                    <td><Pill tone={o.payment_method}>{o.payment_method}</Pill></td>
                    <td><Pill tone={o.payment_verification_status || o.payment_status}>{o.payment_verification_label || o.payment_status || "Not verified"}</Pill></td>
                    <td><Pill tone={o.attribution}>{o.attribution}</Pill></td>
                    <td className="px-4"><Pill tone={o.fulfillment_status}>{o.fulfillment_status}</Pill></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </>
  );
}

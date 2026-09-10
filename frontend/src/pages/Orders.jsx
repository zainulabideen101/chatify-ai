import { useEffect, useState } from "react";
import { PageHeader } from "@/components/Layout";
import { Pill, Section, EmptyState, useFetch } from "@/components/bits";
import { api, formatApiErrorDetail, rs } from "@/lib/api";
import { toast } from "sonner";
import { CheckCircle, Warning, X } from "@phosphor-icons/react";

export default function Orders() {
  const { data, loading, reload } = useFetch("/orders");
  const { data: reviewQueue, reload: reloadQueue } = useFetch("/payment-review");
  const [selectedId, setSelectedId] = useState(null);
  const selected = (data || []).find((order) => order.id === selectedId);
  const refresh = () => { reload(); reloadQueue(); };

  return (
    <>
      <PageHeader title="Orders" subtitle="AI & human generated orders" />
      <Section>
        {(reviewQueue || []).length > 0 && (
          <div data-testid="payment-review-queue" className="border border-amber-200 bg-amber-50/60 rounded-lg p-5 space-y-3">
            <div className="flex items-center gap-2"><Warning size={17} weight="fill" className="text-amber-700" /><h2 className="font-display text-sm font-semibold">Payment review queue</h2><span className="text-xs text-amber-800">{reviewQueue.length} waiting</span></div>
            <p className="text-xs text-amber-900/75">Review the customer screenshot before approving or rejecting the payment. Customers receive a normal payment update after your decision.</p>
            <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-2">{reviewQueue.map((order) => <button key={order.id} data-testid={`payment-review-order-${order.id}`} onClick={() => setSelectedId(order.id)} className="text-left border border-amber-200 bg-white rounded-md p-3 hover:border-amber-400 transition-colors"><div className="flex items-center justify-between gap-2"><span className="text-sm font-medium">Order #{order.order_number}</span><span className="text-xs tnum">{rs(order.total)}</span></div><p className="text-xs text-muted-foreground mt-1">{(order.items || []).map((item) => `${item.title} ×${item.qty}`).join(", ") || "Bank-transfer payment"}</p><span className="inline-flex items-center gap-1 text-[11px] text-amber-800 mt-2"><Warning size={12} /> Payment review required</span></button>)}</div>
          </div>
        )}

        <div className={selected ? "xl:grid xl:grid-cols-[minmax(0,1fr)_390px] xl:items-start gap-5" : ""}>
          <div>
            {loading ? "Loading…" : !data?.length ? (
              <EmptyState title="No orders yet" hint="Orders created by the AI on WhatsApp and synced from Shopify will appear here." />
            ) : (
              <div className="border border-border/60 rounded-lg bg-card overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="text-[11px] uppercase text-muted-foreground text-left bg-muted/40"><tr><th className="px-4 py-2.5 font-medium">Order</th><th className="font-medium">Items</th><th className="font-medium">Total</th><th className="font-medium">Payment</th><th className="font-medium">Payment verification</th><th className="font-medium">Attribution</th><th className="px-4 font-medium">Status</th></tr></thead>
                  <tbody className="divide-y divide-border/60">{data.map((order) => <tr key={order.id} data-testid={`order-${order.id}`} onClick={() => setSelectedId(order.id)} className={`hover:bg-muted/40 cursor-pointer ${selectedId === order.id ? "bg-muted/50" : ""}`}><td className="px-4 py-3 font-medium tnum">#{order.order_number}</td><td className="text-muted-foreground">{(order.items || []).map((item) => `${item.title} ×${item.qty}`).join(", ")}</td><td className="tnum font-medium">{rs(order.total)}</td><td><Pill tone={order.payment_method}>{order.payment_method}</Pill></td><td><Pill tone={order.payment_verification_status || order.payment_status}>{order.payment_verification_label || order.payment_status || "Not verified"}</Pill></td><td><Pill tone={order.attribution}>{order.attribution}</Pill></td><td className="px-4"><Pill tone={order.fulfillment_status}>{order.fulfillment_status}</Pill></td></tr>)}</tbody>
                </table>
              </div>
            )}
          </div>
          {selected && <div className="mt-5 xl:mt-0 xl:sticky xl:top-20"><PaymentOrderDetail order={selected} onClose={() => setSelectedId(null)} onDone={() => { refresh(); setSelectedId(null); }} /></div>}
        </div>
      </Section>
    </>
  );
}

function PaymentOrderDetail({ order, onClose, onDone }) {
  const [evidenceUrl, setEvidenceUrl] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let url;
    api.get(`/orders/${order.id}/payment-evidence`, { responseType: "blob" }).then((response) => {
      url = URL.createObjectURL(response.data);
      setEvidenceUrl(url);
    }).catch(() => setEvidenceUrl(null));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [order.id]);

  const decide = async (decision) => {
    setBusy(true);
    try {
      const { data } = await api.post(`/orders/${order.id}/payment-review`, { decision });
      toast.success(decision === "approve" ? "Payment approved" : "Payment rejected");
      if (data.notification !== "sent") toast.info("Decision saved, but WhatsApp notification was not delivered.");
      onDone();
    } catch (error) {
      toast.error(formatApiErrorDetail(error.response?.data?.detail) || "Could not save payment decision");
    } finally { setBusy(false); }
  };

  return (
    <div data-testid="payment-order-detail" className="border border-border/60 rounded-lg bg-card overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-5 py-4 border-b border-border/60"><div><h2 className="font-display text-sm font-semibold">Order #{order.order_number} payment evidence</h2><p className="text-xs text-muted-foreground mt-1">Seller-only review details</p></div><button aria-label="Close payment evidence" onClick={onClose} className="text-muted-foreground hover:text-foreground"><X size={18} /></button></div>
      <div className="grid lg:grid-cols-[minmax(0,1fr)_280px] gap-5 p-5">
        <div className="min-h-56 rounded-md border border-dashed border-border bg-muted/20 flex items-center justify-center p-3">{evidenceUrl ? <img src={evidenceUrl} alt={`Payment screenshot for order ${order.order_number}`} className="max-h-80 max-w-full object-contain rounded" /> : <p className="text-xs text-muted-foreground">Payment screenshot unavailable</p>}</div>
        <div className="space-y-3 text-sm"><div className="flex items-center gap-2"><CheckCircle size={16} className="text-[hsl(var(--whatsapp))]" /><span className="font-medium">{order.payment_verification_label || "Payment review required"}</span></div><DetailRow label="Extracted amount" value={order.payment_extracted_amount == null ? "—" : rs(order.payment_extracted_amount)} /><DetailRow label="Order total" value={rs(order.total)} /><DetailRow label="AI recommendation" value={order.payment_ai_recommendation || "—"} /><DetailRow label="Shopify payment sync" value={order.shopify_payment_sync_status || "Not connected"} /><DetailRow label="Reference" value={order.payment_reference || "—"} /><DetailRow label="Transaction date" value={order.payment_transaction_date || "—"} /><DetailRow label="Confidence" value={order.payment_verification_confidence == null ? "—" : `${Math.round(order.payment_verification_confidence * 100)}%`} /><div><p className="text-xs text-muted-foreground mb-1">Verification reasons</p><ul className="list-disc pl-4 text-xs space-y-1">{(order.payment_verification_reasons || []).length ? order.payment_verification_reasons.map((reason, index) => <li key={index}>{reason}</li>) : <li>No reasons recorded</li>}</ul></div>{["review_required", "ai_marked_paid_screenshot"].includes(order.payment_verification_status) && <div className="flex gap-2 pt-2"><button data-testid="approve-payment-btn" disabled={busy} onClick={() => decide("approve")} className="flex-1 h-9 rounded-md bg-[hsl(var(--whatsapp))] text-white text-sm hover:opacity-90 disabled:opacity-50">Approve & notify</button><button data-testid="reject-payment-btn" disabled={busy} onClick={() => decide("reject")} className="flex-1 h-9 rounded-md border border-destructive/40 text-destructive text-sm hover:bg-destructive/5 disabled:opacity-50">Reject & notify</button></div>}</div>
      </div>
    </div>
  );
}

const DetailRow = ({ label, value }) => <div className="flex items-start justify-between gap-3"><span className="text-xs text-muted-foreground">{label}</span><span className="text-xs font-medium text-right break-all">{value}</span></div>;

import { useState } from "react";
import { PageHeader } from "@/components/Layout";
import { Section, useFetch, EmptyState } from "@/components/bits";
import { api, rs } from "@/lib/api";
import { toast } from "sonner";
import { CheckCircle, Warning } from "@phosphor-icons/react";

export default function Billing() {
  const { data: summary, loading, reload } = useFetch("/billing/summary");
  const { data: planData } = useFetch("/billing/plans");
  const [busy, setBusy] = useState(false);

  const chooseFree = async () => {
    setBusy(true);
    try {
      await api.post("/billing/plan", { plan_id: "free" });
      toast.success("Free plan selected");
      reload();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Unable to update plan");
    } finally {
      setBusy(false);
    }
  };

  if (loading || !summary) return <><PageHeader title="Plans & billing" /><Section>Loading…</Section></>;
  const plans = planData?.plans || [];
  const limitLabel = (limit) => limit == null ? "Unlimited" : `${limit} new chats/day`;
  const usagePercent = summary.limit == null ? 0 : Math.min((summary.used / summary.limit) * 100, 100);

  return (
    <>
      <PageHeader title="Plans & billing" subtitle="Simple pricing for Pakistani ecommerce sellers" />
      <Section>
        <div className="border border-border/60 rounded-lg bg-card p-5 space-y-4">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-[11px] uppercase tracking-wide text-muted-foreground">Current plan</p>
              <h2 className="font-display text-xl font-semibold mt-1">{summary.plan.name}</h2>
              <p className="text-sm text-muted-foreground mt-1">{limitLabel(summary.limit)} · {summary.plan.price_pkr ? `${rs(summary.plan.price_pkr)}/month` : "Free"}</p>
            </div>
            <span className="inline-flex items-center gap-1 text-xs text-green-700"><CheckCircle weight="fill" size={15} /> {summary.subscription.status}</span>
          </div>
          <div>
            <div className="flex justify-between text-xs mb-1.5"><span>Today’s unique customer chats</span><span className="tnum">{summary.used}{summary.limit == null ? "" : ` / ${summary.limit}`}</span></div>
            <div className="h-2 rounded-full bg-muted overflow-hidden"><div className="h-full rounded-full bg-[hsl(var(--whatsapp))] transition-all" style={{ width: `${summary.limit == null ? 3 : usagePercent}%` }} /></div>
            <p className="text-xs text-muted-foreground mt-2">Resets at midnight Pakistan time. When the limit is reached, new chats still arrive for human replies while AI pauses.</p>
          </div>
        </div>

        <div>
          <div className="flex items-center gap-2 mb-4"><h2 className="font-display text-sm font-semibold">Choose a plan</h2><span className="text-xs text-muted-foreground">Prices in PKR, billed monthly</span></div>
          {plans.length === 0 ? <EmptyState title="Plans unavailable" hint="Refresh to load pricing." /> : <div className="grid md:grid-cols-2 xl:grid-cols-4 gap-4">
            {plans.map((plan) => {
              const current = plan.id === summary.plan.id;
              return <div key={plan.id} className={`border rounded-lg bg-card p-5 flex flex-col ${current ? "border-primary ring-1 ring-primary/20" : "border-border/60"}`}>
                <div className="flex items-start justify-between gap-2"><h3 className="font-display font-semibold">{plan.name}</h3>{current && <span className="text-[10px] uppercase tracking-wide text-primary font-semibold">Current</span>}</div>
                <p className="text-2xl font-display font-semibold mt-4 tnum">{plan.price_pkr ? rs(plan.price_pkr) : "Free"}<span className="text-xs text-muted-foreground font-normal">{plan.price_pkr ? "/month" : ""}</span></p>
                <p className="text-sm text-muted-foreground mt-3 min-h-10">{plan.description}</p>
                <p className="text-sm font-medium mt-4">{limitLabel(plan.daily_unique_customer_limit)}</p>
                <button disabled={current || busy || plan.id !== "free"} onClick={plan.id === "free" ? chooseFree : undefined} className="mt-5 h-9 rounded-md border border-input text-sm hover:bg-muted disabled:opacity-50 disabled:cursor-not-allowed">{current ? "Selected" : plan.id === "free" ? "Select free plan" : "Payment setup pending"}</button>
              </div>;
            })}
          </div>}
        </div>

        <div className="border border-amber-200 bg-amber-50 rounded-lg p-4 text-xs text-amber-900 space-y-2">
          <div className="flex items-center gap-2 font-medium"><Warning weight="fill" size={15} /> Payment methods are not connected</div>
          <p>JazzCash, Easypaisa, and bank-transfer activation will appear here after real merchant credentials and bank details are configured. No paid plan is activated from the browser alone.</p>
        </div>
      </Section>
    </>
  );
}

import { PageHeader } from "@/components/Layout";
import { StatCard, Pill, Section, useFetch } from "@/components/bits";
import { rs } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Warning, TrendUp, ArrowClockwise } from "@phosphor-icons/react";
import { Link } from "react-router-dom";

export default function Dashboard() {
  const { user } = useAuth();
  const { data, loading } = useFetch("/dashboard");
  if (loading || !data) return <><PageHeader title="Dashboard" /><Section>Loading…</Section></>;
  const s = data.stats;

  return (
    <>
      <PageHeader title="AI Sales Overview" subtitle={`${user?.store_name || "Your store"} · Last 30 days`} />
      <Section>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard testid="kpi-conversations" label="Conversations" value={s.conversations} sub="handled" />
          <StatCard testid="kpi-resolution" label="AI Resolution Rate" value={`${s.ai_resolution_rate}%`} accent="text-[hsl(var(--whatsapp))]" />
          <StatCard testid="kpi-orders" label="Orders Generated" value={s.orders_generated} sub={`${s.ai_orders} by AI`} />
          <StatCard testid="kpi-ai-revenue" label="AI-Attributed Revenue" value={rs(s.ai_attributed_revenue)} />
          <StatCard testid="kpi-assisted-revenue" label="AI-Assisted Revenue" value={rs(s.ai_assisted_revenue)} />
          <StatCard testid="kpi-aov" label="Average Order Value" value={rs(s.average_order_value)} />
          <StatCard testid="kpi-conversion" label="Conversion Rate" value={`${s.conversion_rate}%`} />
          <StatCard testid="kpi-recovered" label="Recovered Orders" value={s.recovered_count} sub={rs(s.recovered_revenue)} />
        </div>

        <div className="grid lg:grid-cols-3 gap-6">
          <Panel title="Recent Conversations" className="lg:col-span-2">
            {data.recent_conversations.length === 0 && <p className="text-xs text-muted-foreground py-4">No conversations yet. They appear here once customers message your WhatsApp.</p>}
            <div className="divide-y divide-border/60">
              {data.recent_conversations.map((c) => (
                <Link to="/conversations" key={c.id} className="flex items-center justify-between py-3 hover:bg-muted/40 -mx-2 px-2 rounded-md transition-colors">
                  <div className="min-w-0">
                    <div className="text-sm font-medium">{c.customer_name}</div>
                    <div className="text-xs text-muted-foreground truncate max-w-md">{c.last_message}</div>
                  </div>
                  <Pill tone={c.mode}>{c.mode}</Pill>
                </Link>
              ))}
            </div>
          </Panel>

          <Panel title="Needs Attention" icon={<Warning weight="fill" className="text-amber-500" size={16} />}>
            {data.attention_conversations.length === 0 && <p className="text-xs text-muted-foreground">All clear.</p>}
            <div className="space-y-2">
              {data.attention_conversations.map((c) => (
                <div key={c.id} className="text-sm border border-border/60 rounded-md p-3">
                  <div className="font-medium">{c.customer_name}</div>
                  <div className="text-xs text-muted-foreground truncate">{c.last_message}</div>
                </div>
              ))}
            </div>
          </Panel>
        </div>

        <div className="grid lg:grid-cols-3 gap-6">
          <Panel title="Recent Orders" className="lg:col-span-2">
            {data.recent_orders.length === 0 && <p className="text-xs text-muted-foreground py-4">No orders yet. AI-created and Shopify-synced orders show here.</p>}
            <table className="w-full text-sm">
              <thead className="text-[11px] uppercase text-muted-foreground text-left">
                <tr><th className="py-2 font-medium">Order</th><th className="font-medium">Total</th><th className="font-medium">Payment</th><th className="font-medium">Attribution</th><th className="font-medium">Status</th></tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {data.recent_orders.map((o) => (
                  <tr key={o.id} className="hover:bg-muted/40">
                    <td className="py-2.5 font-medium tnum">#{o.order_number}</td>
                    <td className="tnum">{rs(o.total)}</td>
                    <td><Pill tone={o.payment_method}>{o.payment_method}</Pill></td>
                    <td><Pill tone={o.attribution}>{o.attribution}</Pill></td>
                    <td><Pill tone={o.fulfillment_status}>{o.fulfillment_status}</Pill></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>

          <Panel title="Abandoned Opportunities" icon={<ArrowClockwise size={16} className="text-muted-foreground" />}>
            <div className="space-y-2">
              {data.abandoned_opportunities.map((a) => (
                <div key={a.id} className="text-sm border border-border/60 rounded-md p-3 flex justify-between items-center">
                  <div><div className="font-medium">{a.customer_name}</div><div className="text-xs text-muted-foreground">{a.product} · {a.variant}</div></div>
                  <span className="tnum text-sm font-medium">{rs(a.order_value)}</span>
                </div>
              ))}
            </div>
          </Panel>
        </div>

        <Panel title="Top-Selling Products" icon={<TrendUp size={16} className="text-[hsl(var(--whatsapp))]" />}>
          {data.top_products.length === 0 && <p className="text-xs text-muted-foreground py-4">Connect Shopify to sync products and see best sellers here.</p>}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {data.top_products.map((p) => (
              <div key={p.id} className="border border-border/60 rounded-md overflow-hidden">
                <img src={p.image} alt={p.title} className="h-28 w-full object-cover" />
                <div className="p-3"><div className="text-sm font-medium">{p.title}</div><div className="text-xs text-muted-foreground tnum">{rs(p.price)} · {p.sold_count} sold</div></div>
              </div>
            ))}
          </div>
        </Panel>
      </Section>
    </>
  );
}

function Panel({ title, icon, children, className = "" }) {
  return (
    <div className={`border border-border/60 rounded-lg bg-card p-5 ${className}`}>
      <div className="flex items-center gap-2 mb-4">
        {icon}<h3 className="font-display text-sm font-semibold tracking-tight">{title}</h3>
      </div>
      {children}
    </div>
  );
}

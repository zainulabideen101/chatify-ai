import { PageHeader } from "@/components/Layout";
import { StatCard, Section, useFetch } from "@/components/bits";
import { rs } from "@/lib/api";
import { BarChart, Bar, ResponsiveContainer, XAxis, YAxis, Tooltip, Cell } from "recharts";

export default function Analytics() {
  const { data: attr } = useFetch("/analytics/revenue-attribution");
  const { data: cu } = useFetch("/analytics/cross-upsell");
  const { data: cust } = useFetch("/analytics/customers");
  const { data: overview } = useFetch("/analytics/overview");

  const attrData = attr ? [
    { name: "AI-attributed", value: attr.ai_attributed_revenue },
    { name: "AI-assisted", value: attr.ai_assisted_revenue },
    { name: "Human-only", value: attr.human_only_revenue },
  ] : [];
  const colors = ["hsl(217 91% 60%)", "hsl(238 70% 60%)", "hsl(240 5% 65%)"];

  return (
    <>
      <PageHeader title="Analytics" subtitle="Revenue attribution & AI performance" />
      <Section>
        {overview && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard label="Total Revenue" value={rs(overview.total_revenue)} />
            <StatCard label="AI-Attributed" value={rs(overview.ai_attributed_revenue)} accent="text-blue-600" />
            <StatCard label="Recovery Rate" value={`${overview.recovery_rate}%`} sub={rs(overview.recovered_revenue)} />
            <StatCard label="Human Takeover Rate" value={`${overview.human_takeover_rate}%`} />
          </div>
        )}

        <div className="grid lg:grid-cols-2 gap-6">
          <div className="border border-border/60 rounded-lg bg-card p-5">
            <h3 className="font-display text-sm font-semibold mb-4">Revenue Attribution</h3>
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={attrData} margin={{ left: 10, right: 10 }}>
                <XAxis dataKey="name" tick={{ fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 11 }} axisLine={false} tickLine={false} width={70} tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} />
                <Tooltip formatter={(v) => rs(v)} cursor={{ fill: "hsl(240 5% 96%)" }} />
                <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                  {attrData.map((_, i) => <Cell key={i} fill={colors[i]} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="border border-border/60 rounded-lg bg-card p-5 space-y-4">
            <h3 className="font-display text-sm font-semibold">Cross-sell & Upsell</h3>
            {cu && [["Cross-sell", cu.cross_sell], ["Upsell", cu.upsell]].map(([label, d]) => (
              <div key={label} className="border border-border/60 rounded-md p-4">
                <div className="text-sm font-medium mb-2">{label}</div>
                <div className="grid grid-cols-3 gap-2 text-center">
                  <Mini k="Recommended" v={d.recommendations} />
                  <Mini k="Accepted" v={`${d.acceptance_rate}%`} />
                  <Mini k="Revenue" v={rs(d.revenue)} />
                </div>
              </div>
            ))}
          </div>
        </div>

        {cust && (
          <div className="border border-border/60 rounded-lg bg-card p-5">
            <h3 className="font-display text-sm font-semibold mb-4">Customers</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <Mini k="Total" v={cust.total_customers} />
              <Mini k="Returning" v={cust.returning_customers} />
              <Mini k="New" v={cust.new_customers} />
              <Mini k="Repeat rate" v={`${cust.repeat_rate}%`} />
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              {Object.entries(cust.language_distribution).map(([l, n]) => (
                <span key={l} className="text-xs bg-secondary rounded-md px-2.5 py-1">{l}: <span className="font-medium tnum">{n}</span></span>
              ))}
            </div>
          </div>
        )}
      </Section>
    </>
  );
}
const Mini = ({ k, v }) => (
  <div className="bg-muted/40 rounded-md py-3"><div className="text-base font-semibold tnum text-center">{v}</div><div className="text-[10px] text-muted-foreground text-center">{k}</div></div>
);

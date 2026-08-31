import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export function useFetch(url, deps = []) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const reload = () => {
    setLoading(true);
    api.get(url).then((r) => { setData(r.data); setError(null); })
      .catch((e) => setError(e))
      .finally(() => setLoading(false));
  };
  useEffect(() => { reload(); /* eslint-disable-next-line */ }, deps);
  return { data, loading, error, reload, setData };
}

export function StatCard({ label, value, sub, accent, testid }) {
  return (
    <div data-testid={testid} className="border border-border/60 rounded-lg bg-card p-5 flex flex-col gap-1.5">
      <span className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className={`font-display text-2xl font-semibold tnum ${accent || ""}`}>{value}</span>
      {sub && <span className="text-xs text-muted-foreground">{sub}</span>}
    </div>
  );
}

const STATUS_STYLES = {
  delivered: "bg-green-100 text-green-800",
  active: "bg-green-100 text-green-800",
  cancelled: "bg-red-100 text-red-700",
  refused: "bg-red-100 text-red-700",
  abandoned: "bg-amber-100 text-amber-800",
  recovered: "bg-green-100 text-green-800",
  pending: "bg-amber-100 text-amber-800",
  PENDING: "bg-amber-100 text-amber-800",
  APPROVED: "bg-green-100 text-green-800",
  AI_ACTIVE: "bg-blue-100 text-blue-700",
  HUMAN_ACTIVE: "bg-purple-100 text-purple-700",
  AI: "bg-blue-100 text-blue-700",
  AI_ASSISTED: "bg-indigo-100 text-indigo-700",
  HUMAN: "bg-neutral-200 text-neutral-700",
  LOW: "bg-green-100 text-green-800",
  MEDIUM: "bg-amber-100 text-amber-800",
  HIGH: "bg-red-100 text-red-700",
  COD: "bg-neutral-200 text-neutral-700",
};

export function Pill({ children, tone }) {
  const cls = STATUS_STYLES[tone] || STATUS_STYLES[children] || "bg-secondary text-secondary-foreground";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-medium ${cls}`}>
      {String(children).replace(/_/g, " ")}
    </span>
  );
}

export function EmptyState({ title, hint }) {
  return (
    <div className="border border-dashed border-border rounded-lg py-16 text-center">
      <p className="font-display text-sm font-medium">{title}</p>
      {hint && <p className="text-xs text-muted-foreground mt-1 max-w-md mx-auto">{hint}</p>}
    </div>
  );
}

export function Section({ children }) {
  return <div className="p-8 space-y-6 animate-fade-up">{children}</div>;
}

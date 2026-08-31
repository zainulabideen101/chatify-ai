import { PageHeader } from "@/components/Layout";
import { Pill, Section, EmptyState, useFetch } from "@/components/bits";
import { rs } from "@/lib/api";

export default function Products() {
  const { data, loading } = useFetch("/products");
  return (
    <>
      <PageHeader title="Products" subtitle="Synced from Shopify" />
      <Section>
        {loading ? "Loading…" : !data.length ? (
          <EmptyState title="No products" hint="Connect Shopify to synchronize your catalog, variants and inventory." />
        ) : (
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {data.map((p) => (
              <div key={p.id} data-testid={`product-${p.id}`} className="border border-border/60 rounded-lg bg-card overflow-hidden">
                <img src={p.image} alt={p.title} className="h-40 w-full object-cover" />
                <div className="p-4 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">{p.title}</span>
                    <Pill tone={p.status}>{p.status}</Pill>
                  </div>
                  <div className="text-sm tnum font-semibold">{rs(p.price)}</div>
                  <div className="text-xs text-muted-foreground">{p.sold_count} sold</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>
    </>
  );
}

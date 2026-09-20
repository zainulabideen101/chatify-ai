import { useState } from "react";
import { PageHeader } from "@/components/Layout";
import { Pill, Section, EmptyState, useFetch } from "@/components/bits";
import { api, rs } from "@/lib/api";
import { toast } from "sonner";
import { ArrowClockwise } from "@phosphor-icons/react";

export default function Products() {
  const { data, loading, reload } = useFetch("/products");
  const [syncing, setSyncing] = useState(false);

  const syncFromShopify = async () => {
    setSyncing(true);
    try {
      const { data: result } = await api.post("/integrations/shopify/sync");
      toast.success(`Catalog synced — ${result.products} products, ${result.variants} variants`);
      reload();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Connect Shopify first");
    } finally { setSyncing(false); }
  };

  return (
    <>
      <PageHeader title="Products" subtitle="Synced from Shopify" />
      <Section>
        <div className="flex items-center justify-between mb-4">
          <div data-testid="products-total" className="text-sm text-muted-foreground">
            Total products: <b className="text-foreground tnum">{data?.length ?? 0}</b>
          </div>
          <button data-testid="sync-products-shopify-btn" disabled={syncing} onClick={syncFromShopify} className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md bg-primary text-primary-foreground text-sm hover:bg-primary/90 disabled:opacity-50">
            <ArrowClockwise size={15} className={syncing ? "animate-spin" : ""} /> {syncing ? "Syncing..." : "Sync products from Shopify"}
          </button>
        </div>
        {loading ? "Loading…" : !data.length ? (
          <EmptyState title="No products" hint="Connect Shopify and press “Sync products from Shopify” to load your catalog, variants and inventory." />
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

import { useState } from "react";
import { PageHeader } from "@/components/Layout";
import { Section, useFetch } from "@/components/bits";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { ShoppingBagOpen, CheckCircle, Warning, ArrowClockwise } from "@phosphor-icons/react";

export default function Settings() {
  const { data: merchant, setData: setMerchant } = useFetch("/merchant");
  const { data: status, reload } = useFetch("/integrations/status");
  const [shop, setShop] = useState("");

  const saveProfile = async () => {
    const { data } = await api.put("/merchant", {
      store_name: merchant.store_name, contact_phone: merchant.contact_phone,
    });
    setMerchant(data); toast.success("Profile saved");
  };

  const connectShopify = async () => {
    if (!shop) return toast.error("Enter your .myshopify.com domain");
    try {
      const { data } = await api.post("/integrations/shopify/connect", { shop_domain: shop });
      window.location.href = data.authorize_url;
    } catch (e) {
      toast.error(e.response?.data?.detail || "Shopify Partner app not configured on server");
    }
  };
  const syncShopify = async () => {
    try { await api.post("/integrations/shopify/sync"); toast.success("Sync started"); reload(); }
    catch (e) { toast.error(e.response?.data?.detail || "Connect Shopify first"); }
  };

  const sh = status?.shopify;
  return (
    <>
      <PageHeader title="Settings" subtitle="Store profile & integrations" />
      <Section>
        <div className="grid lg:grid-cols-2 gap-6">
          {merchant && (
            <div className="border border-border/60 rounded-lg bg-card p-6 space-y-4">
              <h3 className="font-display text-sm font-semibold">Store profile</h3>
              <label className="block space-y-1.5"><span className="text-xs font-medium">Store name</span><input data-testid="store-name" value={merchant.store_name || ""} onChange={(e) => setMerchant({ ...merchant, store_name: e.target.value })} className="w-full h-10 px-3 rounded-md border border-input text-sm" /></label>
              <label className="block space-y-1.5"><span className="text-xs font-medium">Contact phone</span><input data-testid="contact-phone" value={merchant.contact_phone || ""} onChange={(e) => setMerchant({ ...merchant, contact_phone: e.target.value })} className="w-full h-10 px-3 rounded-md border border-input text-sm" /></label>
              <div className="flex gap-4 text-sm text-muted-foreground"><span>Currency: <b className="text-foreground">{merchant.currency}</b></span><span>Country: <b className="text-foreground">{merchant.country}</b></span></div>
              <button data-testid="save-profile-btn" onClick={saveProfile} className="h-10 px-4 rounded-md bg-primary text-primary-foreground text-sm hover:bg-primary/90">Save profile</button>
            </div>
          )}

          <div className="border border-border/60 rounded-lg bg-card p-6 space-y-4">
            <div className="flex items-center gap-3">
              <div className="h-11 w-11 rounded-lg bg-[#95BF47]/15 flex items-center justify-center"><ShoppingBagOpen weight="fill" size={22} className="text-[#5E8E3E]" /></div>
              <div className="flex-1"><div className="font-display font-semibold">Shopify</div><div className="text-xs text-muted-foreground">OAuth connection & catalog sync</div></div>
              {sh?.connected
                ? <span className="inline-flex items-center gap-1 text-xs text-green-700"><CheckCircle weight="fill" size={15} /> Connected</span>
                : <span className="inline-flex items-center gap-1 text-xs text-amber-700"><Warning weight="fill" size={15} /> Not connected</span>}
            </div>

            {!sh?.server_configured && (
              <div className="text-xs bg-amber-50 border border-amber-200 text-amber-800 rounded-md p-3">
                Shopify Partner app credentials (SHOPIFY_API_KEY / SHOPIFY_API_SECRET) are not configured on the server. Add them to enable the OAuth flow.
              </div>
            )}

            {sh?.connected ? (
              <div className="space-y-2 text-sm">
                <Row k="Store" v={sh.shop_domain} />
                <Row k="Products" v={sh.products} />
                <Row k="Variants" v={sh.variants} />
                <Row k="Last sync" v={sh.last_sync || "never"} />
                <Row k="Sync errors" v={sh.sync_errors} />
                <button data-testid="sync-shopify-btn" onClick={syncShopify} className="mt-2 inline-flex items-center gap-1.5 h-9 px-3 rounded-md border border-input text-sm hover:bg-muted"><ArrowClockwise size={15} /> Sync catalog</button>
              </div>
            ) : (
              <div className="space-y-3">
                <label className="block space-y-1.5"><span className="text-xs font-medium">Shop domain</span><input data-testid="shop-domain" value={shop} onChange={(e) => setShop(e.target.value)} placeholder="my-store.myshopify.com" className="w-full h-10 px-3 rounded-md border border-input text-sm" /></label>
                <button data-testid="connect-shopify-btn" onClick={connectShopify} className="h-10 px-4 rounded-md bg-primary text-primary-foreground text-sm hover:bg-primary/90">Connect Shopify</button>
              </div>
            )}
          </div>
        </div>

        <div className="text-xs text-muted-foreground border border-border/60 rounded-lg p-4">
          <b className="text-foreground">Foundation build.</b> Shopify OAuth, Meta WhatsApp, and the GPT-5.4 AI agent are wired as real service interfaces — no mocked API responses. Add the credentials above to activate each integration; the architecture does not need to change.
        </div>
      </Section>
    </>
  );
}
const Row = ({ k, v }) => <div className="flex justify-between"><span className="text-muted-foreground">{k}</span><span className="font-medium tnum">{v}</span></div>;

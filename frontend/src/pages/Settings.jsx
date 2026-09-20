import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "@/components/Layout";
import { Section, useFetch } from "@/components/bits";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { ShoppingBagOpen, CheckCircle, Warning, ArrowClockwise } from "@phosphor-icons/react";

export default function Settings() {
  const { data: merchant, setData: setMerchant } = useFetch("/merchant");
  const { data: status, reload } = useFetch("/integrations/status");
  const [params] = useSearchParams();
  const [shop, setShop] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [connecting, setConnecting] = useState(false);

  useEffect(() => {
    if (params.get("shopify") === "connected") {
      toast.success("Shopify connected successfully");
      reload();
    }
  }, [params, reload]);

  const saveProfile = async () => {
    const { data } = await api.put("/merchant", {
      store_name: merchant.store_name, contact_phone: merchant.contact_phone,
    });
    setMerchant(data); toast.success("Profile saved");
  };

  const connectShopify = async () => {
    if (!shop.trim() || !clientId.trim() || !clientSecret.trim())
      return toast.error("Enter your shop domain, Client ID and Client Secret");
    setConnecting(true);
    try {
      const { data } = await api.post("/integrations/shopify/connect", {
        shop_domain: shop.trim(), client_id: clientId.trim(), client_secret: clientSecret.trim(),
      });
      window.location.href = data.authorize_url;
    } catch (e) {
      setConnecting(false);
      toast.error(e.response?.data?.detail || "Could not start the Shopify connection");
    }
  };
  const disconnectShopify = async () => {
    try { await api.post("/integrations/shopify/disconnect"); toast.success("Shopify disconnected"); reload(); }
    catch (e) { toast.error(e.response?.data?.detail || "Could not disconnect Shopify"); }
  };
  const syncShopify = async () => {
    try { await api.post("/integrations/shopify/sync"); toast.success("Catalog synced"); reload(); }
    catch (e) { toast.error(e.response?.data?.detail || "Shopify catalog sync failed"); }
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
              <div className="flex-1"><div className="font-display font-semibold">Shopify</div><div className="text-xs text-muted-foreground">Client ID / Client Secret connection & catalog sync</div></div>
              {sh?.connected
                ? <span className="inline-flex items-center gap-1 text-xs text-green-700"><CheckCircle weight="fill" size={15} /> Connected</span>
                : <span className="inline-flex items-center gap-1 text-xs text-amber-700"><Warning weight="fill" size={15} /> Not connected</span>}
            </div>

            <div className="text-xs bg-blue-50 border border-blue-200 text-blue-800 rounded-md p-3">
              Paste the <b>Client ID</b> and <b>Client Secret</b> of your Shopify app (Shopify Dev Dashboard → your app → Settings). You will be redirected to your store to approve access. Make sure your app's allowed redirect URL includes the callback shown when you press Connect.
            </div>

            {sh?.connected ? (
              <div className="space-y-3 text-sm">
                <Row k="Store" v={sh.shop_domain} />
                <Row k="Products" v={sh.products} />
                <Row k="Variants" v={sh.variants} />
                <Row k="Last sync" v={sh.last_sync || "never"} />
                <Row k="Sync errors" v={sh.sync_errors} />
                <div className="flex gap-2 pt-1">
                  <button data-testid="sync-shopify-btn" onClick={syncShopify} className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md border border-input text-sm hover:bg-muted"><ArrowClockwise size={15} /> Sync catalog</button>
                  <button data-testid="disconnect-shopify-btn" onClick={disconnectShopify} className="h-9 px-3 rounded-md border border-red-200 text-red-700 text-sm hover:bg-red-50">Disconnect</button>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <label className="block space-y-1.5"><span className="text-xs font-medium">Shop domain</span><input data-testid="shop-domain" value={shop} onChange={(e) => setShop(e.target.value)} placeholder="my-store.myshopify.com" className="w-full h-10 px-3 rounded-md border border-input text-sm" /></label>
                <label className="block space-y-1.5"><span className="text-xs font-medium">Client ID</span><input data-testid="shopify-client-id" value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="Shopify app Client ID" className="w-full h-10 px-3 rounded-md border border-input text-sm" /></label>
                <label className="block space-y-1.5"><span className="text-xs font-medium">Client Secret</span><input data-testid="shopify-client-secret" type="password" value={clientSecret} onChange={(e) => setClientSecret(e.target.value)} placeholder="Shopify app Client Secret" className="w-full h-10 px-3 rounded-md border border-input text-sm" /></label>
                <button data-testid="connect-shopify-btn" disabled={connecting} onClick={connectShopify} className="h-10 px-4 rounded-md bg-primary text-primary-foreground text-sm hover:bg-primary/90 disabled:opacity-50">{connecting ? "Redirecting..." : "Connect Shopify"}</button>
              </div>
            )}
          </div>
        </div>

        <div className="text-xs text-muted-foreground border border-border/60 rounded-lg p-4">
          <b className="text-foreground">Shopify connection.</b> Your Shopify app's Client ID and Client Secret authorize the connection; the resulting access token and your credentials are encrypted before storage and never shown again.
        </div>
      </Section>
    </>
  );
}
const Row = ({ k, v }) => <div className="flex justify-between"><span className="text-muted-foreground">{k}</span><span className="font-medium tnum">{v}</span></div>;

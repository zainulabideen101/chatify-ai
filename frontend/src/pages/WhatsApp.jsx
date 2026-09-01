import { useState, useEffect } from "react";
import { PageHeader } from "@/components/Layout";
import { Section, useFetch } from "@/components/bits";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { WhatsappLogo, CheckCircle, Warning } from "@phosphor-icons/react";

export default function WhatsApp() {
  const { data, loading, reload } = useFetch("/integrations/status");
  const { data: embed } = useFetch("/integrations/whatsapp/embedded-config");
  const [form, setForm] = useState({ waba_id: "", phone_number_id: "", display_phone_number: "", access_token: "" });

  useEffect(() => {
    if (!embed?.configured || window.FB) return;
    window.fbAsyncInit = function () {
      window.FB.init({ appId: embed.app_id, autoLogAppEvents: true, xfbml: true, version: embed.graph_version });
    };
    const s = document.createElement("script");
    s.src = "https://connect.facebook.net/en_US/sdk.js";
    s.async = true; s.defer = true;
    document.body.appendChild(s);
  }, [embed]);

  const launchEmbeddedSignup = () => {
    if (!embed?.configured || !window.FB) return toast.error("Meta App not configured on server yet.");
    window.FB.login((response) => {
      if (response.authResponse?.code) {
        toast.info("Signup started — finalizing…");
        // waba_id / phone_number_id arrive via the WA embedded signup message event in production.
        api.post("/integrations/whatsapp/embedded-exchange", { code: response.authResponse.code })
          .then(() => { toast.success("WhatsApp connected"); reload(); })
          .catch((e) => toast.error(e.response?.data?.detail || "Exchange failed"));
      }
    }, { config_id: embed.config_id, response_type: "code", override_default_response_type: true });
  };

  const connect = async () => {
    for (const k of Object.keys(form)) if (!form[k]) return toast.error("All fields required");
    try {
      await api.post("/integrations/whatsapp/connect", form);
      toast.success("WhatsApp connected");
      reload();
    } catch (e) { toast.error("Failed to connect"); }
  };
  const disconnect = async () => { await api.post("/integrations/whatsapp/disconnect"); reload(); };

  const wa = data?.whatsapp;
  return (
    <>
      <PageHeader title="WhatsApp" subtitle="Meta WhatsApp Business Platform" />
      <Section>
        {loading ? "Loading…" : (
          <div className="max-w-2xl space-y-6">
            <div className="border border-border/60 rounded-lg bg-card p-6">
              <div className="flex items-center gap-3 mb-4">
                <div className="h-11 w-11 rounded-lg bg-[hsl(var(--whatsapp))]/10 flex items-center justify-center">
                  <WhatsappLogo weight="fill" size={22} className="text-[hsl(var(--whatsapp))]" />
                </div>
                <div className="flex-1">
                  <div className="font-display font-semibold">WhatsApp Business Account</div>
                  <div className="text-xs text-muted-foreground">Connect your own WABA & phone number via Meta</div>
                </div>
                {wa?.connected ? (
                  <span className="inline-flex items-center gap-1 text-xs text-green-700"><CheckCircle weight="fill" size={15} /> Connected</span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-xs text-amber-700"><Warning weight="fill" size={15} /> Not connected</span>
                )}
              </div>

              {!wa?.server_configured && (
                <div className="text-xs bg-amber-50 border border-amber-200 text-amber-800 rounded-md p-3 mb-4">
                  Meta App credentials (META_APP_ID / META_APP_SECRET) are not configured on the server. Embedded Signup requires these; you can still register a WABA manually below.
                </div>
              )}

              {wa?.connected ? (
                <div className="space-y-2">
                  <Row k="Phone number" v={wa.display_phone_number} />
                  <Row k="WABA ID" v={wa.waba_id} />
                  <button data-testid="wa-disconnect" onClick={disconnect} className="mt-3 h-9 px-3 rounded-md border border-destructive/40 text-destructive text-sm hover:bg-destructive/5">Disconnect</button>
                </div>
              ) : (
                <div className="space-y-3">
                  <Input label="WhatsApp Business Account ID" value={form.waba_id} onChange={(e) => setForm({ ...form, waba_id: e.target.value })} testid="wa-waba" />
                  <Input label="Phone Number ID" value={form.phone_number_id} onChange={(e) => setForm({ ...form, phone_number_id: e.target.value })} testid="wa-phone-id" />
                  <Input label="Display phone number" value={form.display_phone_number} onChange={(e) => setForm({ ...form, display_phone_number: e.target.value })} testid="wa-display" placeholder="+92 300 …" />
                  <Input label="Access token" value={form.access_token} onChange={(e) => setForm({ ...form, access_token: e.target.value })} testid="wa-token" placeholder="Stored encrypted" />
                  <button data-testid="wa-connect" onClick={connect} className="h-10 px-4 rounded-md bg-primary text-primary-foreground text-sm hover:bg-primary/90">Connect WhatsApp</button>
                </div>
              )}
            </div>

            <div className="border border-border/60 rounded-lg bg-card p-6 text-sm text-muted-foreground">
              <div className="font-display font-semibold text-foreground mb-2">Webhooks</div>
              <p>Incoming customer messages arrive via a verified Meta webhook. Configure your Meta app webhook URL to:</p>
              <code className="block mt-2 mono text-xs bg-muted rounded p-2 break-all">{process.env.REACT_APP_BACKEND_URL}/api/webhooks/whatsapp</code>
              <p className="mt-2">Access tokens are stored encrypted at rest. The AI never sends messages until the LLM is connected.</p>
            </div>
          </div>
        )}
      </Section>
    </>
  );
}
const Row = ({ k, v }) => <div className="flex justify-between text-sm py-0.5"><span className="text-muted-foreground">{k}</span><span className="font-medium">{v}</span></div>;
function Input({ label, testid, ...props }) {
  return <label className="block space-y-1.5"><span className="text-xs font-medium">{label}</span><input data-testid={testid} className="w-full h-10 px-3 rounded-md border border-input text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" {...props} /></label>;
}

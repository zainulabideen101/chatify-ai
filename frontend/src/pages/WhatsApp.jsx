import { useState, useEffect } from "react";
import { PageHeader } from "@/components/Layout";
import { Section, useFetch } from "@/components/bits";
import { api, formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";
import { WhatsappLogo, CheckCircle, Warning } from "@phosphor-icons/react";

export default function WhatsApp() {
  const { data, loading, reload } = useFetch("/integrations/status");
  const { data: embed } = useFetch("/integrations/whatsapp/embedded-config");
  const [form, setForm] = useState({ waba_id: "", phone_number_id: "", display_phone_number: "", access_token: "" });
  const [signupAssets, setSignupAssets] = useState({});
  const [signupBusy, setSignupBusy] = useState(false);

  useEffect(() => {
    if (!embed?.configured) return undefined;
    const onMessage = (event) => {
      if (!/(^|\\.)facebook\\.com$/.test(new URL(event.origin).hostname)) return;
      let message;
      try { message = typeof event.data === "string" ? JSON.parse(event.data) : event.data; } catch { return; }
      if (message?.type !== "WA_EMBEDDED_SIGNUP") return;
      if (message.event === "ERROR" || message.event === "CANCEL") {
        setSignupBusy(false);
        toast.error(message.data?.error_message || "Meta WhatsApp signup was cancelled or failed.");
        return;
      }
      if (String(message.event || "").startsWith("FINISH")) {
        const assets = message.data || {};
        setSignupAssets((current) => ({ ...current, ...assets }));
      }
    };
    const initialize = () => {
      if (window.FB && !window.FB.__saylaniInitialized) {
        window.FB.init({ appId: embed.app_id, cookie: true, xfbml: false, version: embed.graph_version });
        window.FB.__saylaniInitialized = true;
      }
    };
    window.fbAsyncInit = initialize;
    window.addEventListener("message", onMessage);
    if (window.FB) initialize();
    else if (!document.getElementById("facebook-jssdk")) {
      const script = document.createElement("script");
      script.id = "facebook-jssdk";
      script.src = "https://connect.facebook.net/en_US/sdk.js";
      script.async = true; script.defer = true;
      document.body.appendChild(script);
    }
    return () => window.removeEventListener("message", onMessage);
  }, [embed]);

  const completeEmbeddedSignup = async (response) => {
    const code = response?.authResponse?.code;
    if (!code) { setSignupBusy(false); return toast.error("Meta did not return an authorization code."); }
    try {
      await api.post("/integrations/whatsapp/embedded-exchange", {
        code,
        waba_id: signupAssets.waba_id || signupAssets.wabaId || null,
        phone_number_id: signupAssets.phone_number_id || signupAssets.phoneNumberId || null,
      });
      toast.success("WhatsApp connected");
      setSignupBusy(false);
      reload();
    } catch (e) {
      setSignupBusy(false);
      toast.error(formatApiErrorDetail(e.response?.data?.detail) || "WhatsApp connection failed");
    }
  };

  const launchEmbeddedSignup = () => {
    if (!embed?.configured || !window.FB) return toast.error("Meta Embedded Signup is not configured on the server.");
    setSignupBusy(true);
    window.FB.login((response) => { void completeEmbeddedSignup(response); }, {
      config_id: embed.config_id,
      response_type: "code",
      override_default_response_type: true,
      extras: { setup: {} },
    });
  };

  const connect = async () => {
    for (const k of Object.keys(form)) if (!form[k]) return toast.error("All fields required");
    try {
      await api.post("/integrations/whatsapp/connect", form);
      toast.success("WhatsApp connected");
      setForm({ waba_id: "", phone_number_id: "", display_phone_number: "", access_token: "" });
      reload();
    } catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail) || "WhatsApp validation failed"); reload(); }
  };
  const validate = async () => {
    try { await api.post("/integrations/whatsapp/validate"); toast.success("WhatsApp credentials are valid"); reload(); }
    catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail) || "WhatsApp validation failed"); reload(); }
  };
  const disconnect = async () => { await api.post("/integrations/whatsapp/disconnect"); reload(); };

  const wa = data?.whatsapp;
  const statusLabel = wa?.status === "connecting" ? "Connecting" : wa?.status === "error" ? "Error" : wa?.connected ? "Connected" : "Not connected";
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
                <span className={`inline-flex items-center gap-1 text-xs ${wa?.status === "error" ? "text-red-700" : wa?.connected ? "text-green-700" : "text-amber-700"}`}>
                  {wa?.connected ? <CheckCircle weight="fill" size={15} /> : <Warning weight="fill" size={15} />} {statusLabel}
                </span>
              </div>

              {!wa?.server_configured && !wa?.connected && (
                <div className="text-xs bg-amber-50 border border-amber-200 text-amber-800 rounded-md p-3 mb-4">
                  Meta App credentials are not configured on the server. Add META_APP_ID, META_APP_SECRET, META_CONFIG_ID, and META_VERIFY_TOKEN in backend secrets to enable Embedded Signup and verified webhooks.
                </div>
              )}
              {wa?.last_error && <div className="text-xs bg-red-50 border border-red-200 text-red-800 rounded-md p-3 mb-4">{wa.last_error}</div>}
              {embed?.configured && (
                <button data-testid="wa-embedded-connect" onClick={launchEmbeddedSignup} disabled={signupBusy || wa?.status === "connecting"} className="w-full h-10 mb-4 rounded-md bg-primary text-primary-foreground text-sm hover:bg-primary/90 disabled:opacity-60">{signupBusy || wa?.status === "connecting" ? "Connecting…" : "Connect with Meta Embedded Signup"}</button>
              )}

              {wa?.connected ? (
                <div className="space-y-2">
                  <Row k="Phone number" v={wa.display_phone_number} />
                  <Row k="WABA ID" v={wa.waba_id} />
                  <Row k="Phone status" v={wa.phone_status || "—"} />
                  <Row k="Quality" v={wa.quality_rating || "—"} />
                  <div className="flex gap-2 mt-3"><button data-testid="wa-validate" onClick={validate} className="h-9 px-3 rounded-md border border-input text-sm hover:bg-muted">Validate connection</button><button data-testid="wa-disconnect" onClick={disconnect} className="h-9 px-3 rounded-md border border-destructive/40 text-destructive text-sm hover:bg-destructive/5">Disconnect</button></div>
                </div>
              ) : (
                <div className="space-y-3">
                  <Input label="WhatsApp Business Account ID" value={form.waba_id} onChange={(e) => setForm({ ...form, waba_id: e.target.value })} testid="wa-waba" />
                  <Input label="Phone Number ID" value={form.phone_number_id} onChange={(e) => setForm({ ...form, phone_number_id: e.target.value })} testid="wa-phone-id" />
                  <Input label="Display phone number" value={form.display_phone_number} onChange={(e) => setForm({ ...form, display_phone_number: e.target.value })} testid="wa-display" placeholder="+92 300 …" />
                  <Input label="Access token" type="password" value={form.access_token} onChange={(e) => setForm({ ...form, access_token: e.target.value })} testid="wa-token" placeholder="Stored encrypted" />
                  <button data-testid="wa-connect" onClick={connect} className="h-10 px-4 rounded-md bg-primary text-primary-foreground text-sm hover:bg-primary/90">Connect WhatsApp</button>
                </div>
              )}
            </div>

            <div className="border border-border/60 rounded-lg bg-card p-6 text-sm text-muted-foreground">
              <div className="font-display font-semibold text-foreground mb-2">Webhooks</div>
              <p>Incoming messages and delivery statuses arrive through the verified Meta webhook:</p>
              <code className="block mt-2 mono text-xs bg-muted rounded p-2 break-all">{process.env.REACT_APP_BACKEND_URL}/api/webhooks/whatsapp</code>
              <p className="mt-2">Access tokens are stored encrypted at rest, validated with Meta before connection, and never returned to React. Configure Meta webhook verification and subscribe the connected WABA before going live.</p>
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

import { useState } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import { formatApiErrorDetail } from "@/lib/api";
import { Storefront, WhatsappLogo } from "@phosphor-icons/react";

export default function Login() {
  const { user, login, register } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState({ email: "", password: "", name: "", store_name: "" });
  const [loading, setLoading] = useState(false);

  if (user) return <Navigate to="/" replace />;

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      if (mode === "login") await login(form.email, form.password);
      else await register(form);
      toast.success("Welcome");
      navigate("/");
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    } finally {
      setLoading(false);
    }
  };

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  return (
    <div className="min-h-screen grid lg:grid-cols-2">
      {/* Left brand panel */}
      <div className="hidden lg:flex flex-col justify-between bg-primary text-primary-foreground p-12 relative overflow-hidden grain">
        <div className="flex items-center gap-2.5 relative z-10">
          <div className="h-8 w-8 rounded-md bg-primary-foreground flex items-center justify-center">
            <Storefront weight="fill" className="text-primary" size={18} />
          </div>
          <span className="font-display font-semibold text-lg">Chatify AI</span>
        </div>
        <div className="relative z-10 space-y-4">
          <div className="inline-flex items-center gap-2 text-xs bg-primary-foreground/10 px-3 py-1.5 rounded-full">
            <WhatsappLogo weight="fill" className="text-[hsl(var(--whatsapp))]" size={14} />
            AI WhatsApp Commerce Manager
          </div>
          <h2 className="font-display text-4xl font-semibold leading-tight tracking-tighter max-w-md">
            An AI sales employee for your Shopify store.
          </h2>
          <p className="text-primary-foreground/60 max-w-sm text-sm leading-relaxed">
            Understands Roman Urdu, checks live inventory, sends real product media, takes orders on WhatsApp, and recovers abandoned carts — autonomously.
          </p>
        </div>
        <p className="text-primary-foreground/40 text-xs relative z-10">Multi-tenant · Shopify · Meta WhatsApp · GPT-5.4</p>
      </div>

      {/* Right form */}
      <div className="flex items-center justify-center p-8">
        <form onSubmit={submit} className="w-full max-w-sm space-y-5" data-testid="auth-form">
          <div>
            <h1 className="font-display text-2xl font-semibold tracking-tight">
              {mode === "login" ? "Sign in" : "Create your store"}
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              {mode === "login" ? "Access your merchant dashboard" : "Start managing WhatsApp sales with AI"}
            </p>
          </div>

          {mode === "register" && (
            <>
              <Field label="Store name" value={form.store_name} onChange={set("store_name")} testid="input-store-name" placeholder="TechGear Threads" />
              <Field label="Your name" value={form.name} onChange={set("name")} testid="input-name" placeholder="Ali Raza" />
            </>
          )}
          <Field label="Email" type="email" value={form.email} onChange={set("email")} testid="input-email" placeholder="you@store.com" />
          <Field label="Password" type="password" value={form.password} onChange={set("password")} testid="input-password" placeholder="••••••••" />

          <button
            type="submit"
            disabled={loading}
            data-testid="submit-auth-btn"
            className="w-full h-10 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-60"
          >
            {loading ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
          </button>

          <p className="text-sm text-muted-foreground text-center">
            {mode === "login" ? "No account yet? " : "Already have an account? "}
            <button
              type="button"
              data-testid="toggle-auth-mode"
              onClick={() => setMode(mode === "login" ? "register" : "login")}
              className="text-foreground font-medium hover:underline"
            >
              {mode === "login" ? "Sign up" : "Sign in"}
            </button>
          </p>
        </form>
      </div>
    </div>
  );
}

function Field({ label, testid, ...props }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-foreground/80">{label}</span>
      <input
        required
        data-testid={testid}
        className="w-full h-10 px-3 rounded-md border border-input bg-card text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 transition-shadow"
        {...props}
      />
    </label>
  );
}

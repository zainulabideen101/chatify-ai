import { useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import {
  SquaresFour, ChatCircleDots, Package, Users, TShirt, Images,
  Robot, WhatsappLogo, FileText, BookOpen, ChartLineUp, Gear, SignOut, Storefront, List, X, ArrowClockwise,
} from "@phosphor-icons/react";

const NAV = [
  { to: "/", label: "Dashboard", icon: SquaresFour, end: true },
  { to: "/conversations", label: "Conversations", icon: ChatCircleDots },
  { to: "/orders", label: "Orders", icon: Package },
  { to: "/customers", label: "Customers", icon: Users },
  { to: "/products", label: "Products", icon: TShirt },
  { to: "/media", label: "Media Library", icon: Images },
  { to: "/ai-agent", label: "AI Agent", icon: Robot },
  { to: "/whatsapp", label: "WhatsApp", icon: WhatsappLogo },
  { to: "/templates", label: "Templates", icon: FileText },
  { to: "/knowledge", label: "Knowledge Base", icon: BookOpen },
  { to: "/analytics", label: "Analytics", icon: ChartLineUp },
  { to: "/settings", label: "Settings", icon: Gear },
  { to: "/billing", label: "Plans & billing", icon: FileText },
  { to: "/recovery", label: "Recovery", icon: ArrowClockwise },
];

export function Layout({ children }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  return (
    <div className="flex min-h-screen bg-background">
      {/* Mobile overlay */}
      {open && <div className="fixed inset-0 z-30 bg-black/40 lg:hidden" onClick={() => setOpen(false)} />}

      {/* Sidebar */}
      <aside className={`w-64 shrink-0 border-r border-border/60 bg-card flex flex-col fixed h-screen z-40 transition-transform duration-200 ${open ? "translate-x-0" : "-translate-x-full"} lg:translate-x-0`}>
        <div className="h-16 flex items-center gap-2.5 px-6 border-b border-border/60">
          <div className="h-7 w-7 rounded-md bg-primary flex items-center justify-center">
            <Storefront weight="fill" className="text-primary-foreground" size={16} />
          </div>
          <span className="font-display font-semibold text-[15px] tracking-tight">Saylani AI</span>
          <button className="ml-auto lg:hidden text-muted-foreground" onClick={() => setOpen(false)}><X size={18} /></button>
        </div>
        <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-0.5">
          {NAV.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                onClick={() => setOpen(false)}
                data-testid={`nav-${item.label.toLowerCase().replace(/\s+/g, "-")}`}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors ${
                    isActive
                      ? "bg-primary text-primary-foreground font-medium"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground"
                  }`
                }
              >
                <Icon size={18} weight="regular" />
                {item.label}
              </NavLink>
            );
          })}
        </nav>
        <div className="border-t border-border/60 p-3">
          <div className="flex items-center gap-3 px-2 py-1.5">
            <div className="h-8 w-8 rounded-full bg-secondary flex items-center justify-center text-xs font-semibold">
              {(user?.name || "?").slice(0, 1)}
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium truncate">{user?.store_name || user?.name}</div>
              <div className="text-xs text-muted-foreground truncate">{user?.email}</div>
            </div>
            <button
              data-testid="logout-btn"
              onClick={async () => { await logout(); navigate("/login"); }}
              className="text-muted-foreground hover:text-destructive transition-colors"
              title="Sign out"
            >
              <SignOut size={18} />
            </button>
          </div>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 lg:ml-64 min-w-0">
        {/* Mobile top bar */}
        <div className="h-14 lg:hidden flex items-center gap-3 px-4 border-b border-border/60 bg-white sticky top-0 z-20">
          <button data-testid="mobile-menu-btn" onClick={() => setOpen(true)} className="text-foreground"><List size={22} /></button>
          <span className="font-display font-semibold text-sm">Saylani AI</span>
        </div>
        {children}
      </main>
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }) {
  return (
    <header className="min-h-16 sticky top-0 z-20 flex items-center justify-between gap-3 px-5 sm:px-8 py-3 border-b border-border/60 bg-white/70 backdrop-blur-xl">
      <div className="min-w-0">
        <h1 className="font-display text-lg font-semibold tracking-tight truncate">{title}</h1>
        {subtitle && <p className="text-xs text-muted-foreground truncate">{subtitle}</p>}
      </div>
      {actions}
    </header>
  );
}

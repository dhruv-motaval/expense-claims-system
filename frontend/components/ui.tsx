"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { setToken, useMe } from "@/lib/auth";
import { STATUS_COLORS, STATUS_LABELS, tierMeta } from "@/lib/format";

const ICONS: Record<string, React.ReactNode> = {
  dash: <><rect x="3" y="3" width="7" height="9" rx="1" /><rect x="14" y="3" width="7" height="5" rx="1" /><rect x="14" y="12" width="7" height="9" rx="1" /><rect x="3" y="16" width="7" height="5" rx="1" /></>,
  files: <><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z" /><path d="M14 2v5h5M9 13h6M9 17h6" /></>,
  plus: <><circle cx="12" cy="12" r="9" /><path d="M12 8v8M8 12h8" /></>,
  plusl: <path d="M12 5v14M5 12h14" />,
  chart: <><path d="M3 3v18h18" /><path d="M7 16v-5M12 16V8M17 16v-8" /></>,
  warn: <><path d="m21.7 18-8-14a2 2 0 0 0-3.4 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3" /><path d="M12 9v4M12 17h.01" /></>,
  undo: <><path d="M9 14 4 9l5-5" /><path d="M20 20v-7a4 4 0 0 0-4-4H4" /></>,
  check: <><circle cx="12" cy="12" r="9" /><path d="m9 12 2 2 4-4" /></>,
  x: <><circle cx="12" cy="12" r="9" /><path d="m15 9-6 6M9 9l6 6" /></>,
  up: <><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="m17 8-5-5-5 5M12 3v12" /></>,
  arr: <path d="M5 12h14M12 5l7 7-7 7" />,
  out: <><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="m16 17 5-5-5-5M21 12H9" /></>,
  receipt: <><path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1V2l-2 1-2-1-2 1-2-1-2 1-2-1z" /><path d="M8 8h8M8 12h8" /></>,
  wallet: <><path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1" /><path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  shield: <><path d="M20 13c0 5-3.5 7.5-7.7 8.9a1 1 0 0 1-.6 0C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.2-2.7a1.2 1.2 0 0 1 1.6 0C14.5 3.8 17 5 19 5a1 1 0 0 1 1 1z" /><path d="m9 12 2 2 4-4" /></>,
  trend: <><path d="m22 7-8.5 8.5-5-5L2 17" /><path d="M16 7h6v6" /></>,
  users: <><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8" /></>,
};

export function Icon({ name, className = "h-4 w-4" }: { name: string; className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {ICONS[name]}
    </svg>
  );
}

export const initials = (name?: string) => (name || "?").split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();

export function Avatar({ name, size = "sm" }: { name?: string; size?: "sm" | "md" }) {
  return (
    <span className={`inline-flex shrink-0 items-center justify-center rounded-full bg-blue-100 font-semibold text-blue-700 ${size === "md" ? "h-8 w-8 text-xs" : "h-6 w-6 text-[10px]"}`}>
      {initials(name)}
    </span>
  );
}

const pill = "inline-flex h-[22px] items-center gap-1.5 whitespace-nowrap rounded-full px-2 text-[11px] font-semibold";

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`${pill} ${STATUS_COLORS[status] || "bg-slate-100 text-slate-600"}`}
      title={status === "SENT_BACK" ? "A reviewer returned this claim for you to fix and resubmit" : undefined}>
      <i className="h-1.5 w-1.5 rounded-full bg-current" />
      {STATUS_LABELS[status] || status}
    </span>
  );
}

export function TierBadge({ tier }: { tier: string | null | undefined }) {
  const meta = tierMeta(tier);
  if (!meta) return null;
  return (
    <span className={`${pill} ${meta.cls}`} title={meta.title}>
      <Icon name="warn" className="h-3 w-3" />
      {meta.label}
    </span>
  );
}

export function LimitBadge({ status }: { status: string }) {
  const map: Record<string, [string, string]> = {
    NORMAL: ["On track", "bg-emerald-100 text-emerald-800"],
    NEAR_LIMIT: ["Near limit", "bg-amber-100 text-amber-800"],
    OVER_LIMIT: ["Over limit", "bg-red-100 text-red-800"],
  };
  const [label, cls] = map[status] || [status, "bg-slate-100 text-slate-600"];
  return (
    <span className={`${pill} ${cls}`}>
      <i className="h-1.5 w-1.5 rounded-full bg-current" />
      {label}
    </span>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const { me, loading } = useMe();
  const router = useRouter();
  const pathname = usePathname();

  if (loading) return <main className="p-8 text-slate-500">Loading...</main>;
  if (!me) return null;

  const role = me.role;
  const home = role === "MANAGER" ? "/dashboard/manager" : role === "FINANCE" ? "/dashboard/finance" : "/dashboard/staff";
  const nav = [
    { href: home, label: "Dashboard", icon: "dash" },
    { href: "/claims", label: role === "STAFF" ? "My claims" : "Claims", icon: "files" },
    { href: "/claims/new", label: "New claim", icon: "plus" },
    ...(role === "FINANCE" ? [{ href: "/reports", label: "Reports", icon: "chart" }] : []),
  ];
  // longest matching href wins so /claims/new does not also light up /claims
  const active = [...nav].sort((a, b) => b.href.length - a.href.length).find((n) => pathname === n.href || pathname.startsWith(n.href + "/"));

  return (
    <div className="flex min-h-screen flex-col bg-slate-100 text-slate-800 md:flex-row">
      <aside className="flex shrink-0 flex-col gap-1 bg-slate-900 p-3 md:sticky md:top-0 md:h-screen md:w-60">
        <Link href="/" className="flex items-center gap-2.5 px-2 pb-3 pt-1 md:pb-5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-600 font-bold text-white">₹</span>
          <span>
            <span className="block text-sm font-semibold text-white">Expense Claims</span>
            <span className="block text-[11px] text-slate-500">Claims workspace</span>
          </span>
        </Link>
        <p className="hidden px-2 pb-1.5 pt-2 text-[10px] font-semibold tracking-widest text-slate-500 md:block">WORKSPACE</p>
        <nav className="flex gap-1 overflow-x-auto md:flex-col">
          {nav.map((n) => (
            <Link key={n.href} href={n.href}
              className={`flex items-center gap-2.5 whitespace-nowrap rounded-lg px-2.5 py-2 text-[13px] font-medium transition focus:outline-none focus:ring-2 focus:ring-blue-400 ${active?.href === n.href ? "bg-slate-800 text-white" : "text-slate-400 hover:bg-slate-800/60 hover:text-white"}`}>
              <Icon name={n.icon} className={`h-[18px] w-[18px] ${active?.href === n.href ? "text-blue-400" : ""}`} />
              {n.label}
            </Link>
          ))}
        </nav>
        <div className="hidden flex-1 md:block" />
        <div className="mt-2 flex items-center gap-2.5 border-t border-slate-800 px-2 pt-3">
          <Avatar name={me.name} size="md" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13px] font-semibold text-white">{me.name}</p>
            <p className="text-[11px] capitalize text-slate-500">{role.toLowerCase()}</p>
          </div>
          <button onClick={() => { setToken(null); router.push("/login"); }} aria-label="Log out" title="Log out"
            className="rounded-md p-1.5 text-slate-500 transition hover:bg-slate-800 hover:text-white focus:outline-none focus:ring-2 focus:ring-blue-400">
            <Icon name="out" />
          </button>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center border-b border-slate-200 bg-white px-4 text-[13px] text-slate-500 sm:px-6">
          Expense Claims <span className="mx-1.5 text-slate-300">/</span>
          <span className="font-semibold text-slate-900">{active?.label || "Claims"}</span>
        </header>
        <main className="w-full max-w-7xl flex-1 space-y-5 p-4 sm:p-6">{children}</main>
      </div>
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-[22px] font-semibold tracking-tight text-slate-900">{title}</h1>
        {subtitle && <p className="mt-1 text-[13px] text-slate-500">{subtitle}</p>}
      </div>
      {actions && <div className="flex gap-2">{actions}</div>}
    </div>
  );
}

export const btn = {
  primary: "inline-flex h-9 items-center justify-center gap-1.5 rounded-lg bg-blue-600 px-3.5 text-[13px] font-semibold text-white transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-45",
  secondary: "inline-flex h-9 items-center justify-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 text-[13px] font-semibold text-slate-700 transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-45",
};

export function Panel({ title, subtitle, action, children, className = "" }: {
  title?: string; subtitle?: string; action?: React.ReactNode; children: React.ReactNode; className?: string;
}) {
  return (
    <section className={`rounded-xl border border-slate-200 bg-white ${className}`}>
      {title && (
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3.5">
          <div>
            <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
            {subtitle && <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

const TONES: Record<string, string> = {
  blue: "bg-blue-100 text-blue-700", green: "bg-emerald-100 text-emerald-700", amber: "bg-amber-100 text-amber-700",
  red: "bg-red-100 text-red-700", violet: "bg-violet-100 text-violet-700", slate: "bg-slate-100 text-slate-600",
};

export function Card({ title, value, sub, icon = "wallet", tone = "blue" }: {
  title: string; value: string; sub?: React.ReactNode; icon?: string; tone?: keyof typeof TONES;
}) {
  return (
    <div className="flex flex-col gap-2.5 rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between text-xs font-medium text-slate-500">
        {title}
        <span className={`flex h-7 w-7 items-center justify-center rounded-lg ${TONES[tone]}`}><Icon name={icon} /></span>
      </div>
      <p className="text-[26px] font-bold tracking-tight text-slate-900">{value}</p>
      {sub && <div className="text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

export function ErrorBox({ message }: { message: string | null }) {
  if (!message) return null;
  return <p className="rounded-lg border border-red-200 bg-red-50 p-2.5 text-sm text-red-700" role="alert">{message}</p>;
}

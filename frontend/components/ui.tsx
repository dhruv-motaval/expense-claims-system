"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { setToken, useMe } from "@/lib/auth";
import { STATUS_COLORS, STATUS_LABELS, tierMeta } from "@/lib/format";

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${STATUS_COLORS[status] || "bg-gray-100"}`}
      title={status === "SENT_BACK" ? "A reviewer returned this claim for you to fix and resubmit" : undefined}>
      {STATUS_LABELS[status] || status}
    </span>
  );
}

export function TierBadge({ tier }: { tier: string | null | undefined }) {
  const meta = tierMeta(tier);
  if (!meta) return null;
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${meta.cls}`} title={meta.title}>
      {meta.label}
    </span>
  );
}

export function LimitBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    NORMAL: "bg-emerald-100 text-emerald-800",
    NEAR_LIMIT: "bg-amber-100 text-amber-800",
    OVER_LIMIT: "bg-red-100 text-red-800",
  };
  const label = status.replace("_", " ");
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${map[status] || "bg-gray-100"}`}>
      {status === "NEAR_LIMIT" ? "⚠ " : status === "OVER_LIMIT" ? "✋ " : ""}{label}
    </span>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const { me, loading } = useMe();
  const router = useRouter();
  const pathname = usePathname();

  if (loading) return <main className="p-8 text-gray-500">Loading...</main>;
  if (!me) return null;

  const role = me.role;
  const nav = [
    ...(role === "MANAGER" ? [{ href: "/dashboard/manager", label: "Dashboard" }] : []),
    ...(role === "FINANCE" ? [{ href: "/dashboard/finance", label: "Dashboard" }] : []),
    ...(role === "STAFF" ? [{ href: "/dashboard/staff", label: "Dashboard" }] : []),
    { href: "/claims", label: "Claims" },
    { href: "/claims/new", label: "New Claim" },
    ...(role === "FINANCE" ? [{ href: "/reports", label: "Reports" }] : []),
  ];

  return (
    <div className="min-h-screen flex flex-col bg-slate-50">
      <header className="sticky top-0 z-10 bg-white/95 backdrop-blur border-b border-slate-200 px-4 sm:px-6 py-3 flex justify-between items-center shadow-sm">
        <Link href="/" className="flex items-center gap-2 font-bold tracking-tight text-slate-900">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-600 text-white">₹</span>
          <span>Expense Claims</span>
        </Link>
        <div className="flex items-center gap-3 text-sm">
          <div className="hidden sm:block text-right">
            <p className="font-semibold text-slate-800">{me.name}</p>
            <p className="text-[11px] uppercase tracking-wide text-slate-500">{role}</p>
          </div>
          <button
            onClick={() => { setToken(null); router.push("/login"); }}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-slate-600 hover:border-slate-300 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            Log out
          </button>
        </div>
      </header>
      <div className="flex flex-1 flex-col md:flex-row">
        <nav className="w-full md:w-56 shrink-0 border-b md:border-b-0 md:border-r border-slate-200 bg-white p-3 md:p-4">
          <div className="flex md:block gap-1 overflow-x-auto">
            {nav.map((n) => {
              const active = pathname === n.href || (n.href !== "/" && pathname.startsWith(n.href + "/"));
              return (
                <Link key={n.href} href={n.href} className={`block whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium transition ${active ? "bg-blue-50 text-blue-700" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"}`}>
                  {n.label}
                </Link>
              );
            })}
          </div>
        </nav>
        <main className="w-full max-w-6xl flex-1 p-4 sm:p-6 lg:p-8">{children}</main>
      </div>
    </div>
  );
}

export function Card({ title, value, sub }: { title: string; value: string; sub?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md">
      <p className="text-[11px] font-bold uppercase tracking-wider text-slate-500">{title}</p>
      <p className="mt-1 text-2xl font-bold tracking-tight text-slate-900">{value}</p>
      {sub && <div className="mt-2">{sub}</div>}
    </div>
  );
}

export function ErrorBox({ message }: { message: string | null }) {
  if (!message) return null;
  return <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded p-2" role="alert">{message}</p>;
}

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { setToken, useMe } from "@/lib/auth";
import { STATUS_COLORS } from "@/lib/format";

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${STATUS_COLORS[status] || "bg-gray-100"}`}>
      {status}
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
    <div className="min-h-screen flex flex-col">
      <header className="bg-white border-b px-6 py-3 flex justify-between items-center">
        <Link href="/" className="font-bold">Expense Claims</Link>
        <div className="flex items-center gap-3 text-sm">
          <span className="font-medium">{me.name}</span>
          <span className="text-gray-500">{role}</span>
          <button
            onClick={() => { setToken(null); router.push("/login"); }}
            className="text-blue-600 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-500 rounded"
          >
            Log out
          </button>
        </div>
      </header>
      <div className="flex flex-1">
        <nav className="w-48 bg-white border-r p-4 space-y-1 text-sm">
          {nav.map((n) => (
            <Link key={n.href} href={n.href} className="block px-3 py-2 rounded hover:bg-gray-100">
              {n.label}
            </Link>
          ))}
        </nav>
        <main className="flex-1 p-6 max-w-5xl">{children}</main>
      </div>
    </div>
  );
}

export function Card({ title, value, sub }: { title: string; value: string; sub?: React.ReactNode }) {
  return (
    <div className="bg-white rounded-lg shadow p-4">
      <p className="text-xs font-semibold text-gray-500 uppercase">{title}</p>
      <p className="text-2xl font-bold mt-1">{value}</p>
      {sub && <div className="mt-1">{sub}</div>}
    </div>
  );
}

export function ErrorBox({ message }: { message: string | null }) {
  if (!message) return null;
  return <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded p-2" role="alert">{message}</p>;
}

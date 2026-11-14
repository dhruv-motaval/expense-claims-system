"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { setToken } from "@/lib/auth";
import { Avatar, Icon } from "@/components/ui";

const DEMO = [
  { name: "Riya Patel", role: "Staff", email: "riya@example.com" },
  { name: "Neha Shah", role: "Staff · near limit", email: "neha@example.com" },
  { name: "Vikram Joshi", role: "Staff", email: "vikram@example.com" },
  { name: "Arjun Mehta", role: "Manager", email: "arjun@example.com" },
  { name: "Priya Desai", role: "Finance", email: "priya@example.com" },
];

const FEATURES = [
  ["up", "Receipt-first claims", "Upload a PDF or image and the details are extracted for you."],
  ["shield", "Duplicate protection", "Tiered checks stop the same receipt from being paid twice."],
  ["users", "Clear approvals", "Manager and finance queues with a full audit trail."],
];

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("neha@example.com");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function login(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Login failed");
      setToken(data.token);
      const role = data.user.role;
      router.push(role === "MANAGER" ? "/dashboard/manager" : role === "FINANCE" ? "/dashboard/finance" : "/dashboard/staff");
    } catch (err: any) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen">
      <aside className="hidden w-[44%] flex-col justify-between bg-slate-900 p-12 text-white lg:flex">
        <div>
          <div className="flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-600 font-bold">₹</span>
            <span className="text-base font-semibold">Expense Claims</span>
          </div>
          <h2 className="mt-12 text-3xl font-semibold leading-tight tracking-tight">Claims reviewed, approved and paid — in one workflow.</h2>
          <p className="mt-4 text-[15px] leading-relaxed text-slate-400">
            Staff submit receipts, managers approve, finance pays. Every step is audited and every duplicate is caught.
          </p>
          <ul className="mt-6 space-y-5">
            {FEATURES.map(([icon, title, body]) => (
              <li key={title} className="flex items-start gap-3.5">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-800 text-blue-400"><Icon name={icon} className="h-[18px] w-[18px]" /></span>
                <span>
                  <span className="block text-sm font-semibold">{title}</span>
                  <span className="block text-[13px] text-slate-400">{body}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
        <p className="text-xs text-slate-500">© 2026 Expense Claims</p>
      </aside>
      <section className="flex flex-1 items-center justify-center bg-white p-6">
        <div className="w-full max-w-sm space-y-5">
          <div>
            <h1 className="text-2xl font-semibold text-slate-900">Sign in</h1>
            <p className="mt-1.5 text-sm text-slate-500">Use your work email to continue.</p>
          </div>
          <form onSubmit={login} className="space-y-4">
            <div>
              <label htmlFor="email" className="mb-1.5 block text-xs font-semibold text-slate-700">Work email</label>
              <input
                id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
                className="h-10 w-full rounded-lg border border-slate-300 px-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            {error && <p className="text-sm text-red-600" role="alert">{error}</p>}
            <button
              type="submit" disabled={busy}
              className="h-10 w-full rounded-lg bg-blue-600 text-sm font-semibold text-white transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1 disabled:opacity-50"
            >
              {busy ? "Signing in..." : "Sign in"}
            </button>
            <p className="text-center text-xs text-slate-500">Demo accounts use password123 (no password check)</p>
          </form>
          <div className="space-y-2 border-t border-slate-200 pt-4">
            <p className="text-[11px] font-semibold tracking-widest text-slate-500">DEMO ACCOUNTS</p>
            {DEMO.map((d) => (
              <button
                key={d.email} type="button" onClick={() => setEmail(d.email)}
                className={`flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-blue-500 ${email === d.email ? "border-blue-500 bg-blue-50/50" : "border-slate-200"}`}
              >
                <Avatar name={d.name} />
                <span className="flex-1 text-sm font-semibold text-slate-900">{d.name}</span>
                <span className="text-xs text-slate-500">{d.role}</span>
              </button>
            ))}
          </div>
        </div>
      </section>
    </main>
  );
}

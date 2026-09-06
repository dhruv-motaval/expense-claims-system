"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { setToken } from "@/lib/auth";

const DEMO = [
  { name: "Riya Patel", role: "Staff", email: "riya@example.com" },
  { name: "Neha Shah", role: "Staff (near limit)", email: "neha@example.com" },
  { name: "Vikram Joshi", role: "Staff", email: "vikram@example.com" },
  { name: "Arjun Mehta", role: "Manager", email: "arjun@example.com" },
  { name: "Priya Desai", role: "Finance", email: "priya@example.com" },
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
    <main className="min-h-screen flex items-center justify-center p-6">
      <div className="w-full max-w-sm space-y-6">
        <h1 className="text-2xl font-bold text-center">Expense Claims</h1>
        <form onSubmit={login} className="bg-white rounded-lg shadow p-6 space-y-4">
          <div>
            <label htmlFor="email" className="block text-sm font-medium mb-1">Email</label>
            <input
              id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
              className="w-full border rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          {error && <p className="text-sm text-red-600" role="alert">{error}</p>}
          <button
            type="submit" disabled={busy}
            className="w-full bg-blue-600 text-white rounded py-2 font-medium hover:bg-blue-700 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {busy ? "Signing in..." : "Sign in"}
          </button>
          <p className="text-xs text-gray-500 text-center">Demo accounts use password123 (no password check)</p>
        </form>
        <div className="bg-white rounded-lg shadow p-4">
          <p className="text-xs font-semibold text-gray-500 uppercase mb-2">Demo accounts</p>
          <ul className="divide-y">
            {DEMO.map((d) => (
              <li key={d.email}>
                <button
                  onClick={() => setEmail(d.email)}
                  className="w-full text-left py-2 px-1 hover:bg-gray-50 rounded flex justify-between text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <span className="font-medium">{d.name}</span>
                  <span className="text-gray-500">{d.role}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </main>
  );
}

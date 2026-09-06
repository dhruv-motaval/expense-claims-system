"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/auth";
import { inr, fmtDate } from "@/lib/format";
import { Card, ErrorBox, Shell } from "@/components/ui";
import { LimitBadge } from "@/components/ui";

export default function FinanceDashboard() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState<number | null>(null);

  function load() {
    api("/dashboard/finance").then(setData).catch((e) => setError(e.message));
  }
  useEffect(load, []);

  async function pay(id: number) {
    setBusy(id);
    setActionError("");
    try {
      await api(`/claims/${id}/pay`, { method: "POST" });
      load();
    } catch (e: any) {
      setActionError(e.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Shell>
      <div className="flex justify-between items-center mb-4">
        <h1 className="text-xl font-bold">Finance Dashboard</h1>
        <Link href="/reports" className="text-blue-600 hover:underline text-sm">Reports →</Link>
      </div>
      {error && <p className="text-red-600">{error}</p>}
      {data && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
            <Card title="Monthly Spend" value={inr(data.monthly_spend)} />
            <Card title="Awaiting Payment" value={inr(data.awaiting_payment)} />
            <Card title="Paid This Month" value={inr(data.paid_this_month)} />
            <Card title="Over Limit" value={String(data.over_limit)} />
          </div>

          <h2 className="text-sm font-semibold text-gray-500 uppercase mb-2">Employees vs Limits</h2>
          <table className="w-full bg-white rounded-lg shadow text-sm mb-6">
            <thead>
              <tr className="text-left text-xs text-gray-500 uppercase border-b">
                <th className="px-3 py-2">Employee</th>
                <th className="px-3 py-2 text-right">Spend</th>
                <th className="px-3 py-2 text-right">Limit</th>
                <th className="px-3 py-2 text-right">Projected</th>
                <th className="px-3 py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {data.employees.map((e: any) => (
                <tr key={e.id} className="border-b last:border-0">
                  <td className="px-3 py-2">{e.name}</td>
                  <td className="px-3 py-2 text-right">{inr(e.spend)}</td>
                  <td className="px-3 py-2 text-right">{inr(e.limit)}</td>
                  <td className="px-3 py-2 text-right text-gray-500">{inr(e.projected)}</td>
                  <td className="px-3 py-2"><LimitBadge status={e.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>

          <h2 className="text-sm font-semibold text-gray-500 uppercase mb-2">Payment Queue</h2>
          <ErrorBox message={actionError} />
          {data.payment_queue.length === 0 ? (
            <p className="text-gray-500 text-sm py-4">Nothing awaiting payment.</p>
          ) : (
            <table className="w-full bg-white rounded-lg shadow text-sm">
              <thead>
                <tr className="text-left text-xs text-gray-500 uppercase border-b">
                  <th className="px-3 py-2">Claim</th>
                  <th className="px-3 py-2">Employee</th>
                  <th className="px-3 py-2">Merchant</th>
                  <th className="px-3 py-2">Date</th>
                  <th className="px-3 py-2 text-right">Amount</th>
                  <th className="px-3 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {data.payment_queue.map((c: any) => (
                  <tr key={c.id} className="border-b last:border-0">
                    <td className="px-3 py-2">
                      <Link href={`/claims/${c.id}`} className="text-blue-600 hover:underline">{c.claim_number}</Link>
                    </td>
                    <td className="px-3 py-2">{c.user_name}</td>
                    <td className="px-3 py-2">{c.merchant}</td>
                    <td className="px-3 py-2">{fmtDate(c.transaction_date)}</td>
                    <td className="px-3 py-2 text-right font-medium">{inr(c.amount)}</td>
                    <td className="px-3 py-2 text-right">
                      <button
                        onClick={() => pay(c.id)} disabled={busy === c.id}
                        className="bg-violet-600 text-white rounded px-3 py-1 text-xs font-medium hover:bg-violet-700 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-violet-500"
                      >
                        {busy === c.id ? "Paying..." : "Mark as Paid"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h2 className="text-sm font-semibold text-gray-500 uppercase mb-2 mt-6">Spend by Category</h2>
          <table className="w-full bg-white rounded-lg shadow text-sm">
            <tbody>
              {data.categories.map((c: any) => (
                <tr key={c.category} className="border-b last:border-0">
                  <td className="px-3 py-2">{c.category}</td>
                  <td className="px-3 py-2 text-right font-medium">{inr(c.total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Shell>
  );
}

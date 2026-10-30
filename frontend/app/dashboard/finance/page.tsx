"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/auth";
import { inr, fmtDate, tierMeta } from "@/lib/format";
import { Card, ErrorBox, Shell } from "@/components/ui";
import { LimitBadge } from "@/components/ui";

export default function FinanceDashboard() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState<number | null>(null);
  const [justFor, setJustFor] = useState<number | null>(null);      // claim id in the tier-C modal
  const [justification, setJustification] = useState("");

  function load() {
    api("/dashboard/finance").then(setData).catch((e) => setError(e.message));
  }
  useEffect(load, []);

  async function pay(id: number, just?: string) {
    setBusy(id);
    setActionError("");
    try {
      await api(`/claims/${id}/pay`, { method: "POST", body: just ? JSON.stringify({ justification: just }) : undefined });
      setJustFor(null);
      setJustification("");
      load();
    } catch (e: any) {
      setActionError(e.message);
      // tier-C gate: the server demands a typed justification — open the modal
      if (/justification/.test(e.message)) { setJustFor(id); }
      // CAS race: someone else just paid it — tell the user plainly
      else if (/changed by another action/.test(e.message)) { setJustFor(null); load(); }
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
                {data.payment_queue.map((c: any) => {
                  const meta = tierMeta(c.duplicate_tier);
                  return (
                  <tr key={c.id} className={`border-b last:border-0 ${c.duplicate_tier ? "bg-amber-50/50" : ""}`}>
                    <td className="px-3 py-2">
                      <Link href={`/claims/${c.id}`} className="text-blue-600 hover:underline">{c.claim_number}</Link>
                      {meta && <span className={`ml-2 px-1.5 py-0.5 rounded text-[10px] font-semibold ${meta.cls}`} title={meta.title}>{meta.label}</span>}
                    </td>
                    <td className="px-3 py-2">{c.user_name}</td>
                    <td className="px-3 py-2">
                      {c.merchant}
                      {(c.receipt_no || c.trans_time) && (
                        <span className="ml-2 font-mono text-xs text-gray-500 uppercase">
                          {c.receipt_no ? `#${c.receipt_no}` : ""}{c.trans_time ? ` · ${c.trans_time}` : ""}
                        </span>
                      )}
                      {c.duplicate_compare && (
                        <span className="ml-2 text-[10px] text-gray-500">
                          matched paid {c.matched_claim_number}: receipt {c.duplicate_compare.receipt_no?.equal ? "match" : (c.receipt_no ? "differs" : "absent")}, time {c.duplicate_compare.time?.equal ? "match" : (c.trans_time ? "differs" : "absent")}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2">{fmtDate(c.transaction_date)}</td>
                    <td className="px-3 py-2 text-right font-medium">{inr(c.amount)}</td>
                    <td className="px-3 py-2 text-right">
                      <button
                        onClick={() => (c.duplicate_tier === "C" ? setJustFor(c.id) : pay(c.id))}
                        disabled={busy === c.id}
                        className={`text-white rounded px-3 py-1 text-xs font-medium disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-violet-500 ${c.duplicate_tier ? "bg-amber-600 hover:bg-amber-700" : "bg-violet-600 hover:bg-violet-700"}`}
                      >
                        {busy === c.id ? "Paying..." : c.duplicate_tier === "C" ? "Pay anyway (justify)…" : "Mark as Paid"}
                      </button>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          )}

          {justFor !== null && (
            <div className="bg-amber-50 border border-amber-300 rounded-lg shadow p-4 my-4 max-w-md space-y-2">
              <p className="text-sm font-semibold">Pay a possible duplicate (tier C)</p>
              <p className="text-xs text-gray-600">
                Same merchant, date and amount were already paid. Confirm this is a genuine
                separate transaction. The justification and the anchor comparison are stored
                on the audit trail.
              </p>
              <textarea rows={3} value={justification} onChange={(e) => setJustification(e.target.value)}
                placeholder="Min 20 characters — e.g. confirmed with the vendor this is a second trip the same day"
                className="w-full border rounded px-3 py-2 text-sm" />
              <div className="flex gap-2">
                <button onClick={() => pay(justFor, justification.trim())}
                  disabled={busy === justFor || justification.trim().length < 20}
                  className="bg-violet-600 text-white rounded px-4 py-1.5 text-sm font-medium disabled:opacity-50">
                  {busy === justFor ? "Paying..." : "Pay with justification"}
                </button>
                <button onClick={() => { setJustFor(null); setJustification(""); }}
                  className="border rounded px-4 py-1.5 text-sm font-medium hover:bg-gray-100">Cancel</button>
              </div>
            </div>
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

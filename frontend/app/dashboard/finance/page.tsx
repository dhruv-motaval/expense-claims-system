"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/auth";
import { inr, fmtDate } from "@/lib/format";
import { btn, Card, ErrorBox, Icon, PageHeader, Panel, Shell, Avatar, TierBadge } from "@/components/ui";
import { LimitBadge } from "@/components/ui";

export default function FinanceDashboard() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState<number | null>(null);
  const [justFor, setJustFor] = useState<number | null>(null);      // claim id in the tier-C modal
  const [justification, setJustification] = useState("");
  const [returnFor, setReturnFor] = useState<number | null>(null);  // claim id in return-to-manager modal
  const [returnReason, setReturnReason] = useState("");

  function load() {
    api("/dashboard/finance").then(setData).catch((e) => setError(e.message));
  }
  useEffect(load, []);

  async function returnToManager(id: number) {
    setBusy(id);
    setActionError("");
    try {
      await api(`/claims/${id}/return-to-manager`, { method: "POST", body: JSON.stringify({ reason: returnReason }) });
      setReturnFor(null);
      setReturnReason("");
      load();
    } catch (e: any) {
      setActionError(e.message);
      if (/changed by another action/.test(e.message)) { setReturnFor(null); load(); }
    } finally {
      setBusy(null);
    }
  }

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

  const th = "px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500";
  const td = "px-4 py-3";
  const dialog = "max-w-md space-y-2 rounded-xl border border-amber-300 bg-amber-50 p-4 shadow-sm";
  const field = "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500";

  return (
    <Shell>
      <PageHeader
        title="Finance dashboard"
        subtitle="Payments and spend control."
        actions={<Link href="/reports" className={btn.secondary}><Icon name="chart" /> Reports</Link>}
      />
      {error && <p className="text-red-600">{error}</p>}
      {data && (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Card title="Monthly spend" value={inr(data.monthly_spend)} icon="wallet" tone="blue" />
            <Card title="Awaiting payment" value={inr(data.awaiting_payment)} icon="clock" tone="amber" />
            <Card title="Paid this month" value={inr(data.paid_this_month)} icon="check" tone="green" />
            <Card title="Over limit" value={String(data.over_limit)} icon="warn" tone="red" />
          </div>

          <div className="grid gap-4 xl:grid-cols-3">
            <Panel title="Payment queue" subtitle="Approved claims ready to pay" className="xl:col-span-2">
              <ErrorBox message={actionError} />
              {data.payment_queue.length === 0 ? (
                <p className="px-4 py-6 text-sm text-slate-500">Nothing awaiting payment.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-[13px]">
                    <thead>
                      <tr className="border-b border-slate-200 bg-slate-50">
                        <th className={th}>Claim</th>
                        <th className={th}>Employee</th>
                        <th className={th}>Merchant</th>
                        <th className={th}>Date</th>
                        <th className={`${th} text-right`}>Amount</th>
                        <th className={th}></th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.payment_queue.map((c: any) => (
                        <tr key={c.id} className={`border-b border-slate-100 last:border-0 ${c.duplicate_tier ? "bg-amber-50/60" : "hover:bg-slate-50"}`}>
                          <td className={td}>
                            <Link href={`/claims/${c.id}`} className="font-semibold text-blue-600 hover:underline">{c.claim_number}</Link>
                            {c.duplicate_tier && <div className="mt-1"><TierBadge tier={c.duplicate_tier} /></div>}
                          </td>
                          <td className={td}><span className="flex items-center gap-2"><Avatar name={c.user_name} />{c.user_name}</span></td>
                          <td className={td}>
                            {c.merchant}
                            {(c.receipt_no || c.trans_time) && (
                              <span className="block font-mono text-[11px] uppercase text-slate-500">
                                {c.receipt_no ? `#${c.receipt_no}` : ""}{c.trans_time ? ` · ${c.trans_time}` : ""}
                              </span>
                            )}
                            {c.duplicate_compare && (
                              <span className="block text-[11px] text-slate-500">
                                matched paid {c.matched_claim_number}: receipt {c.duplicate_compare.receipt_no?.equal ? "match" : (c.receipt_no ? "differs" : "absent")}, time {c.duplicate_compare.time?.equal ? "match" : (c.trans_time ? "differs" : "absent")}
                              </span>
                            )}
                          </td>
                          <td className={td}>{fmtDate(c.transaction_date)}</td>
                          <td className={`${td} text-right font-semibold tabular-nums text-slate-900`}>{inr(c.amount)}</td>
                          <td className={`${td} text-right`}>
                            <div className="flex items-center justify-end gap-2">
                              <button
                                onClick={() => setReturnFor(c.id)}
                                disabled={busy === c.id}
                                className="h-8 rounded-lg border border-amber-500 bg-white px-3 text-xs font-semibold text-amber-700 transition hover:bg-amber-50 focus:outline-none focus:ring-2 focus:ring-amber-400 disabled:opacity-50"
                                title="Return this approved claim to its manager with your feedback"
                              >
                                Return
                              </button>
                              <button
                                onClick={() => (c.duplicate_tier === "C" ? setJustFor(c.id) : pay(c.id))}
                                disabled={busy === c.id}
                                className={`h-8 rounded-lg px-3 text-xs font-semibold text-white transition focus:outline-none focus:ring-2 focus:ring-violet-500 disabled:opacity-50 ${c.duplicate_tier ? "bg-amber-600 hover:bg-amber-700" : "bg-violet-600 hover:bg-violet-700"}`}
                              >
                                {busy === c.id ? "Working..." : c.duplicate_tier === "C" ? "Pay anyway…" : "Mark as paid"}
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>

            <Panel title="Spend by category" subtitle="This month">
              <div className="space-y-3.5 p-4">
                {data.categories.map((c: any) => {
                  const max = Math.max(...data.categories.map((x: any) => x.total), 1);
                  return (
                    <div key={c.category} className="flex items-center gap-3 text-[13px]">
                      <span className="w-28 shrink-0 capitalize text-slate-700">{String(c.category).toLowerCase()}</span>
                      <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-200">
                        <div className="h-full rounded-full bg-blue-600" style={{ width: `${(c.total / max) * 100}%` }} />
                      </div>
                      <span className="w-20 text-right font-semibold tabular-nums text-slate-900">{inr(c.total)}</span>
                    </div>
                  );
                })}
              </div>
            </Panel>
          </div>

          {returnFor !== null && (
            <div className={dialog}>
              <p className="text-sm font-semibold">Return claim to its manager</p>
              <p className="text-xs text-slate-600">
                The claim goes back to the manager&apos;s queue as &quot;Submitted&quot; with your feedback.
                Finance never contacts staff directly — the manager relays it.
              </p>
              <input
                value={returnReason}
                onChange={(e) => setReturnReason(e.target.value)}
                placeholder="e.g. Category mismatch: receipt shows accommodation"
                className={field} />
              <div className="flex gap-2">
                <button onClick={() => returnToManager(returnFor)}
                  disabled={busy === returnFor || !returnReason.trim()}
                  className="h-9 rounded-lg bg-amber-600 px-4 text-sm font-semibold text-white disabled:opacity-50">
                  {busy === returnFor ? "Returning..." : "Confirm return to manager"}
                </button>
                <button onClick={() => { setReturnFor(null); setReturnReason(""); }} className={btn.secondary}>Cancel</button>
              </div>
            </div>
          )}

          {justFor !== null && (
            <div className={dialog}>
              <p className="text-sm font-semibold">Pay a possible duplicate (tier C)</p>
              <p className="text-xs text-slate-600">
                Same merchant, date and amount were already paid. Confirm this is a genuine
                separate transaction. The justification and the anchor comparison are stored
                on the audit trail.
              </p>
              <textarea rows={3} value={justification} onChange={(e) => setJustification(e.target.value)}
                placeholder="Min 20 characters — e.g. confirmed with the vendor this is a second trip the same day"
                className={field} />
              <div className="flex gap-2">
                <button onClick={() => pay(justFor, justification.trim())}
                  disabled={busy === justFor || justification.trim().length < 20}
                  className="h-9 rounded-lg bg-violet-600 px-4 text-sm font-semibold text-white disabled:opacity-50">
                  {busy === justFor ? "Paying..." : "Pay with justification"}
                </button>
                <button onClick={() => { setJustFor(null); setJustification(""); }} className={btn.secondary}>Cancel</button>
              </div>
            </div>
          )}

          <Panel title="Employees vs limits" subtitle="Actual and projected spend">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-[13px]">
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50">
                    <th className={th}>Employee</th>
                    <th className={`${th} text-right`}>Spend</th>
                    <th className={`${th} text-right`}>Limit</th>
                    <th className={`${th} text-right`}>Projected</th>
                    <th className={th}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.employees.map((e: any) => (
                    <tr key={e.id} className="border-b border-slate-100 last:border-0">
                      <td className={td}><span className="flex items-center gap-2"><Avatar name={e.name} />{e.name}</span></td>
                      <td className={`${td} text-right font-semibold tabular-nums text-slate-900`}>{inr(e.spend)}</td>
                      <td className={`${td} text-right tabular-nums`}>{inr(e.limit)}</td>
                      <td className={`${td} text-right tabular-nums text-slate-500`}>{inr(e.projected)}</td>
                      <td className={td}><LimitBadge status={e.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </>
      )}
    </Shell>
  );
}

"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/auth";
import { btn, Icon, PageHeader, Panel, Shell } from "@/components/ui";
import ClaimTable from "@/components/claims";

const TABS = [
  ["ALL", "All"], ["DRAFT", "Draft"], ["SUBMITTED", "Submitted"], ["APPROVED", "Approved"],
  ["PAID", "Paid"], ["SENT_BACK", "Returned"], ["REJECTED", "Rejected"],
] as const;

export default function ClaimsPage() {
  const [claims, setClaims] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<string>("ALL");
  const [q, setQ] = useState("");

  useEffect(() => {
    api("/claims").then((d) => setClaims(d.claims || d)).catch((e) => setError(e.message));
  }, []);

  const count = (s: string) => (s === "ALL" ? claims.length : claims.filter((c) => c.status === s).length);
  const needle = q.trim().toLowerCase();
  const shown = claims.filter((c) =>
    (tab === "ALL" || c.status === tab) &&
    (!needle || `${c.claim_number} ${c.merchant} ${c.user_name || ""}`.toLowerCase().includes(needle)));

  return (
    <Shell>
      <PageHeader
        title="Claims"
        subtitle="Claims you can access. Open a claim for the receipt, timeline and actions."
        actions={<Link href="/claims/new" className={btn.primary}><Icon name="plusl" /> New claim</Link>}
      />
      {error && <p className="text-red-600">{error}</p>}
      <Panel>
        <div className="flex flex-col gap-2 border-b border-slate-200 px-4 py-2.5 lg:flex-row lg:items-center">
          <div className="flex flex-1 flex-wrap gap-1" role="tablist">
            {TABS.map(([k, label]) => (
              <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition focus:outline-none focus:ring-2 focus:ring-blue-500 ${tab === k ? "bg-blue-50 text-blue-700" : "text-slate-500 hover:bg-slate-50"}`}>
                {label}
                <span className={`rounded-full px-1.5 text-[11px] ${tab === k ? "bg-blue-100 text-blue-700" : "bg-slate-100 text-slate-500"}`}>{count(k)}</span>
              </button>
            ))}
          </div>
          <label className="sr-only" htmlFor="claim-search">Search claims</label>
          <input id="claim-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search claim, merchant, employee…"
            className="h-9 w-full rounded-lg border border-slate-300 bg-slate-50 px-3 text-[13px] focus:outline-none focus:ring-2 focus:ring-blue-500 lg:w-72" />
        </div>
        <ClaimTable claims={shown} showEmployee />
      </Panel>
    </Shell>
  );
}

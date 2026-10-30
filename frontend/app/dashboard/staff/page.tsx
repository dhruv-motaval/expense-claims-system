"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/auth";
import { inr } from "@/lib/format";
import { Card, Shell } from "@/components/ui";
import ClaimTable, { LimitProgress } from "@/components/claims";

export default function StaffDashboard() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/dashboard/staff").then(setData).catch((e) => setError(e.message));
  }, []);

  return (
    <Shell>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-bold uppercase tracking-widest text-blue-600">Overview</p>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-900">My Dashboard</h1>
          <p className="mt-1 text-sm text-slate-500">Track your expenses, approvals, and payments.</p>
        </div>
        <Link href="/claims/new" className="inline-flex items-center justify-center rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 hover:shadow focus:outline-none focus:ring-2 focus:ring-blue-500">
          <span className="mr-1 text-lg leading-none">+</span> New Claim
        </Link>
      </div>
      {error && <p className="text-red-600">{error}</p>}
      {data && (
        <>
          <h2 className="text-sm font-semibold text-gray-500 uppercase mb-2">This Month</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
            <Card title="Actual Spend" value={inr(data.limit.spend)} />
            <Card title="Monthly Limit" value={inr(data.limit.limit)} />
            <Card title="Remaining" value={inr(data.limit.remaining)} sub={<LimitProgress spend={data.limit.spend} limit={data.limit.limit} />} />
            <Card title="Claims" value={String(data.claims_total)} sub={<span className="text-xs text-gray-500">{data.pending_approval} pending · {data.paid} paid</span>} />
          </div>
          <h2 className="text-sm font-semibold text-gray-500 uppercase mb-2">Recent Claims</h2>
          <ClaimTable claims={data.recent_claims} />
        </>
      )}
    </Shell>
  );
}

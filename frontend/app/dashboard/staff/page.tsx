"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/auth";
import { inr } from "@/lib/format";
import { btn, Card, Icon, PageHeader, Panel, Shell } from "@/components/ui";
import ClaimTable, { LimitProgress } from "@/components/claims";

export default function StaffDashboard() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/dashboard/staff").then(setData).catch((e) => setError(e.message));
  }, []);

  const pct = data?.limit?.limit ? Math.round((data.limit.spend / data.limit.limit) * 100) : 0;

  return (
    <Shell>
      <PageHeader
        title="My dashboard"
        subtitle="Track your expenses, approvals and payments."
        actions={<Link href="/claims/new" className={btn.primary}><Icon name="plusl" /> New claim</Link>}
      />
      {error && <p className="text-red-600">{error}</p>}
      {data && (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Card title="Actual spend" value={inr(data.limit.spend)} icon="wallet" tone="blue" sub="This month" />
            <Card title="Monthly limit" value={inr(data.limit.limit)} icon="shield" tone="slate" sub="Resets next month" />
            <Card title="Remaining" value={inr(data.limit.remaining)} icon="trend" tone={pct >= 80 ? "amber" : "green"}
              sub={<LimitProgress spend={data.limit.spend} limit={data.limit.limit} />} />
            <Card title="Claims" value={String(data.claims_total)} icon="files" tone="violet"
              sub={`${data.pending_approval} pending · ${data.paid} paid`} />
          </div>
          {pct >= 80 && (
            <div className="flex gap-3 rounded-xl border border-amber-300 bg-amber-50 p-3.5 text-sm text-amber-900" role="status">
              <Icon name="warn" className="mt-0.5 h-4 w-4 shrink-0" />
              <p>You have used {pct}% of this month’s limit — {inr(data.limit.remaining)} left.</p>
            </div>
          )}
          <Panel title="Recent claims" subtitle="Your latest submissions"
            action={<Link href="/claims" className="flex items-center gap-1 text-xs font-semibold text-blue-600 hover:underline">View all <Icon name="arr" className="h-3 w-3" /></Link>}>
            <ClaimTable claims={data.recent_claims} />
          </Panel>
        </>
      )}
    </Shell>
  );
}

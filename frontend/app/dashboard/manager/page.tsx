"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/auth";
import { inr } from "@/lib/format";
import { Card, Icon, PageHeader, Panel, Shell } from "@/components/ui";
import ClaimTable, { LimitProgress } from "@/components/claims";

/** A titled queue for one kind of work the manager must handle. */
function Queue({ title, subtitle, count, tone, children }: {
  title: string; subtitle?: string; count: number; tone: "blue" | "amber"; children: React.ReactNode;
}) {
  if (!count) return null;
  const badge = tone === "amber" ? "bg-amber-100 text-amber-800" : "bg-blue-100 text-blue-800";
  return (
    <Panel>
      <div className="flex items-center gap-2 border-b border-slate-200 px-4 py-3.5">
        <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
        <span className={`rounded-full px-2 py-0.5 text-xs font-bold ${badge}`}>{count}</span>
      </div>
      {subtitle && <p className="px-4 pt-3 text-xs text-slate-500">{subtitle}</p>}
      {children}
    </Panel>
  );
}

export default function ManagerDashboard() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/dashboard/manager").then(setData).catch((e) => setError(e.message));
  }, []);

  return (
    <Shell>
      <PageHeader title="Manager dashboard" subtitle="Approvals and team spend." />
      {error && <p className="text-red-600">{error}</p>}
      {data && (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Card title="Pending approval" value={String(data.pending_approval)} icon="clock" tone="blue" />
            <Card title="Approved this month" value={String(data.approved_this_month)} icon="check" tone="green" />
            <Card title="Rejected" value={String(data.rejected)} icon="x" tone="red" />
            <Card title="Team spend" value={inr(data.team_spend)} icon="wallet" tone="violet" />
          </div>

          {/* The two work queues, in the order the manager should handle them */}
          <Queue
            title="Returned by finance — your re-review"
            subtitle="Finance sent these back with a note. Read the note, fix with your team member if needed, then approve or reject."
            count={(data.returned_from_finance || []).length}
            tone="amber"
          >
            <ClaimTable claims={data.returned_from_finance} showEmployee showFinanceNote />
          </Queue>

          <Queue title="New requests — waiting for approval" count={(data.new_requests || []).length} tone="blue">
            <ClaimTable claims={data.new_requests} showEmployee />
          </Queue>

          <Panel title="Team limits" subtitle="Monthly spend against each member’s limit">
            <div className="grid gap-5 p-4 md:grid-cols-2 xl:grid-cols-3">
              {data.team_members.map((m: any) => (
                <div key={m.id}>
                  <p className="mb-1.5 text-[13px] font-medium text-slate-800">{m.name}</p>
                  <LimitProgress spend={m.spend} limit={m.limit} />
                </div>
              ))}
            </div>
          </Panel>

          <Panel title="Team claims">
            <ClaimTable claims={data.claims} showEmployee />
          </Panel>
          <p className="flex items-center gap-1.5 text-xs text-slate-500">
            <Icon name="shield" className="h-3.5 w-3.5" /> You cannot approve your own claim — the server blocks it too.
          </p>
        </>
      )}
    </Shell>
  );
}

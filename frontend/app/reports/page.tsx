"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/auth";
import { inr } from "@/lib/format";
import { Card, PageHeader, Panel, Shell } from "@/components/ui";

const STATUS_BAR: Record<string, string> = {
  DRAFT: "bg-slate-400", SUBMITTED: "bg-blue-500", APPROVED: "bg-emerald-500",
  PAID: "bg-violet-500", REJECTED: "bg-red-500", SENT_BACK: "bg-orange-500",
};

export default function ReportsPage() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/reports/monthly").then(setData).catch((e) => setError(e.message));
  }, []);

  const statuses = data ? Object.entries(data.by_status) as [string, number][] : [];
  const total = statuses.reduce((a, [, n]) => a + n, 0) || 1;
  const max = data ? Math.max(...data.categories.map((c: any) => c.total), 1) : 1;

  return (
    <Shell>
      <PageHeader
        title={`Reports — ${data?.month || ""}`}
        subtitle="Approved + paid claims count as actual spend; submitted shown as projected."
      />
      {error && <p className="text-red-600">{error}</p>}
      {data && (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Card title="Total claims" value={String(data.total_claims)} icon="files" tone="blue" sub="This month" />
            <Card title="Spend categories" value={String(data.categories.length)} icon="chart" tone="violet" sub="With approved spend" />
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            <Panel title="Claims by status" subtitle={`${data.total_claims} claims`}>
              <div className="space-y-4 p-4">
                <div className="flex h-3 overflow-hidden rounded-full bg-slate-200">
                  {statuses.map(([s, n]) => (
                    <div key={s} className={STATUS_BAR[s] || "bg-slate-400"} style={{ width: `${(n / total) * 100}%` }} title={`${s}: ${n}`} />
                  ))}
                </div>
                <ul className="space-y-2 text-[13px]">
                  {statuses.map(([s, n]) => (
                    <li key={s} className="flex items-center justify-between">
                      <span className="flex items-center gap-2 capitalize text-slate-700">
                        <i className={`h-2 w-2 rounded-sm ${STATUS_BAR[s] || "bg-slate-400"}`} />{s.replace("_", " ").toLowerCase()}
                      </span>
                      <span className="font-semibold text-slate-900">{n}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </Panel>
            <Panel title="Spend by category" subtitle="Approved + paid" className="lg:col-span-2">
              <div className="space-y-3.5 p-4">
                {data.categories.map((c: any) => (
                  <div key={c.category} className="flex items-center gap-3 text-[13px]">
                    <span className="w-32 shrink-0 capitalize text-slate-700">{String(c.category).toLowerCase()}</span>
                    <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-200">
                      <div className="h-full rounded-full bg-blue-600" style={{ width: `${(c.total / max) * 100}%` }} />
                    </div>
                    <span className="w-24 text-right font-semibold tabular-nums text-slate-900">{inr(c.total)}</span>
                  </div>
                ))}
                {!data.categories.length && <p className="text-sm text-slate-500">No approved spend this month.</p>}
              </div>
            </Panel>
          </div>
        </>
      )}
    </Shell>
  );
}

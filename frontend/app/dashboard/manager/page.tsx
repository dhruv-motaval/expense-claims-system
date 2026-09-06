"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/auth";
import { inr } from "@/lib/format";
import { Card, Shell } from "@/components/ui";
import ClaimTable, { LimitProgress } from "@/components/claims";

export default function ManagerDashboard() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/dashboard/manager").then(setData).catch((e) => setError(e.message));
  }, []);

  return (
    <Shell>
      <h1 className="text-xl font-bold mb-4">Manager Dashboard</h1>
      {error && <p className="text-red-600">{error}</p>}
      {data && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
            <Card title="Pending Approval" value={String(data.pending_approval)} />
            <Card title="Approved This Month" value={String(data.approved_this_month)} />
            <Card title="Rejected" value={String(data.rejected)} />
            <Card title="Team Spend" value={inr(data.team_spend)} />
          </div>

          <h2 className="text-sm font-semibold text-gray-500 uppercase mb-2">Team Limits</h2>
          <div className="bg-white rounded-lg shadow p-4 mb-6 space-y-4">
            {data.team_members.map((m: any) => (
              <div key={m.id}>
                <p className="text-sm font-medium mb-1">{m.name}</p>
                <LimitProgress spend={m.spend} limit={m.limit} />
              </div>
            ))}
          </div>

          <h2 className="text-sm font-semibold text-gray-500 uppercase mb-2">Team Claims</h2>
          <ClaimTable claims={data.claims} showEmployee />
          <p className="text-xs text-gray-500 mt-2">Note: you cannot approve your own claim — the server blocks it too.</p>
        </>
      )}
    </Shell>
  );
}

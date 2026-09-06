"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/auth";
import { inr } from "@/lib/format";
import { Shell } from "@/components/ui";

export default function ReportsPage() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/reports/monthly").then(setData).catch((e) => setError(e.message));
  }, []);

  return (
    <Shell>
      <h1 className="text-xl font-bold mb-1">Reports — {data?.month || ""}</h1>
      <p className="text-xs text-gray-500 mb-4">Approved + paid claims count as actual spend; submitted shown as projected.</p>
      {error && <p className="text-red-600">{error}</p>}
      {data && (
        <div className="grid md:grid-cols-3 gap-4">
          <div className="bg-white rounded-lg shadow p-4">
            <p className="text-xs font-semibold text-gray-500 uppercase mb-2">Monthly Overview</p>
            <p className="text-3xl font-bold">{data.total_claims}</p>
            <ul className="mt-2 text-sm space-y-1">
              {Object.entries(data.by_status).map(([s, n]) => (
                <li key={s} className="flex justify-between"><span>{s}</span><span className="font-medium">{n as number}</span></li>
              ))}
            </ul>
          </div>
          <div className="bg-white rounded-lg shadow p-4 md:col-span-2">
            <p className="text-xs font-semibold text-gray-500 uppercase mb-2">Spend by Category</p>
            <table className="w-full text-sm">
              <tbody>
                {data.categories.map((c: any) => (
                  <tr key={c.category} className="border-b last:border-0">
                    <td className="py-1.5">{c.category}</td>
                    <td className="py-1.5 text-right font-medium">{inr(c.total)}</td>
                  </tr>
                ))}
                {!data.categories.length && <tr><td className="py-2 text-gray-500">No approved spend this month.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Shell>
  );
}

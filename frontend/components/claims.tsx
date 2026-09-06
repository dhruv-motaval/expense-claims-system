import Link from "next/link";
import { StatusBadge, LimitBadge } from "./ui";
import { inr, fmtDate } from "@/lib/format";

export interface ClaimRow {
  id: number; claim_number: string; user_id: number; user_name?: string;
  merchant: string; transaction_date: string; category: string; amount: number;
  status: string; rejection_reason?: string | null; has_receipt_image?: boolean;
}

export default function ClaimTable({ claims, showEmployee }: { claims: ClaimRow[]; showEmployee?: boolean }) {
  if (!claims.length) return <p className="text-gray-500 text-sm py-4">No claims yet.</p>;
  return (
    <table className="w-full bg-white rounded-lg shadow text-sm">
      <thead>
        <tr className="text-left text-xs text-gray-500 uppercase border-b">
          <th className="px-3 py-2">Claim</th>
          {showEmployee && <th className="px-3 py-2">Employee</th>}
          <th className="px-3 py-2">Merchant</th>
          <th className="px-3 py-2">Date</th>
          <th className="px-3 py-2">Category</th>
          <th className="px-3 py-2 text-right">Amount</th>
          <th className="px-3 py-2">Status</th>
        </tr>
      </thead>
      <tbody>
        {claims.map((c) => (
          <tr key={c.id} className="border-b last:border-0 hover:bg-gray-50">
            <td className="px-3 py-2">
              <Link href={`/claims/${c.id}`} className="text-blue-600 hover:underline font-medium">
                {c.claim_number}
              </Link>
            </td>
            {showEmployee && <td className="px-3 py-2">{c.user_name}</td>}
            <td className="px-3 py-2">{c.merchant}</td>
            <td className="px-3 py-2">{fmtDate(c.transaction_date)}</td>
            <td className="px-3 py-2">{c.category}</td>
            <td className="px-3 py-2 text-right font-medium">{inr(c.amount)}</td>
            <td className="px-3 py-2"><StatusBadge status={c.status} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function LimitProgress({ spend, limit }: { spend: number; limit: number }) {
  const pct = limit ? Math.min((spend / limit) * 100, 100) : 0;
  const status = pct >= 100 ? "OVER_LIMIT" : pct >= 80 ? "NEAR_LIMIT" : "NORMAL";
  const bar = pct >= 100 ? "bg-red-500" : pct >= 80 ? "bg-amber-500" : "bg-emerald-500";
  return (
    <div>
      <div className="h-2 bg-gray-200 rounded-full overflow-hidden" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
        <div className={`h-full ${bar}`} style={{ width: `${pct}%` }} />
      </div>
      <div className="flex justify-between items-center mt-1 text-xs text-gray-600">
        <span>{inr(spend)} / {inr(limit)} ({(limit ? (spend / limit) * 100 : 0).toFixed(1)}%)</span>
        <LimitBadge status={status} />
      </div>
    </div>
  );
}

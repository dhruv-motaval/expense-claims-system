import Link from "next/link";
import { StatusBadge, LimitBadge, Avatar, Icon } from "./ui";
import { inr, fmtDate } from "@/lib/format";

export interface ClaimRow {
  id: number; claim_number: string; user_id: number; user_name?: string;
  merchant: string; transaction_date: string; category: string; amount: number;
  status: string; rejection_reason?: string | null; has_receipt_image?: boolean;
  returned_from_finance?: boolean;
  finance_feedback?: { reason?: string | null; flagged_fields?: string[] } | null;
  returned_by?: string | null;
  /* receipt file location travels with the ticket data */
  receipt_id?: number | null;
  receipt_file?: string | null;
  receipt_storage_tier?: string | null;
  receipt_filename?: string | null;
  receipt_content_type?: string | null;
  receipt_file_size?: number | null;
}

/** Small chip on a claim row: shows at a glance that the original receipt file
 *  is attached, and the tier it physically lives in (hot = active storage). */
export function ReceiptChip({ row }: { row: ClaimRow }) {
  if (!row.has_receipt_image) return null;
  const hot = row.receipt_storage_tier !== "COLD";
  return (
    <span
      title={`Receipt attached: ${row.receipt_filename || "file"} · stored ${hot ? "hot (active)" : "cold (archived)"}`}
      className={`ml-2 inline-flex h-5 items-center gap-1 rounded-md px-1.5 align-middle text-[10px] font-bold uppercase ${hot ? "bg-blue-100 text-blue-700" : "bg-slate-200 text-slate-600"}`}
    >
      <Icon name="receipt" className="h-3 w-3" /> {hot ? "hot" : "cold"}
    </span>
  );
}

const th = "px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500";
const td = "px-4 py-3";

export default function ClaimTable({ claims, showEmployee, showFinanceNote }: { claims: ClaimRow[]; showEmployee?: boolean; showFinanceNote?: boolean }) {
  if (!claims.length) return <p className="px-4 py-6 text-sm text-slate-500">No claims here right now.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-[13px]">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50">
            <th className={th}>Claim</th>
            {showEmployee && <th className={th}>Employee</th>}
            <th className={th}>Merchant</th>
            <th className={th}>Date</th>
            <th className={th}>Category</th>
            <th className={`${th} text-right`}>Amount</th>
            <th className={th}>Status</th>
            {showFinanceNote && <th className={th}>Finance note</th>}
          </tr>
        </thead>
        <tbody>
          {claims.map((c) => (
            <tr key={c.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
              <td className={td}>
                <Link href={`/claims/${c.id}`} className="font-semibold text-blue-600 hover:underline">
                  {c.claim_number}
                </Link>
                <ReceiptChip row={c} />
              </td>
              {showEmployee && (
                <td className={td}><span className="flex items-center gap-2"><Avatar name={c.user_name} />{c.user_name}</span></td>
              )}
              <td className={td}>{c.merchant}</td>
              <td className={td}>{fmtDate(c.transaction_date)}</td>
              <td className={td}><span className="rounded-md bg-slate-100 px-2 py-0.5 text-[11px] font-semibold capitalize text-slate-600">{c.category.toLowerCase()}</span></td>
              <td className={`${td} text-right font-semibold tabular-nums text-slate-900`}>{inr(c.amount)}</td>
              <td className={td}><StatusBadge status={c.status} /></td>
              {showFinanceNote && (
                <td className={`${td} max-w-[26ch]`}>
                  {c.finance_feedback?.reason
                    ? <span className="text-amber-800">“{c.finance_feedback.reason}”<span className="block text-xs text-slate-500">— {c.returned_by || "Finance"}</span></span>
                    : <span className="text-slate-400">—</span>}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function LimitProgress({ spend, limit }: { spend: number; limit: number }) {
  const pct = limit ? Math.min((spend / limit) * 100, 100) : 0;
  const status = pct >= 100 ? "OVER_LIMIT" : pct >= 80 ? "NEAR_LIMIT" : "NORMAL";
  const bar = pct >= 100 ? "bg-red-500" : pct >= 80 ? "bg-amber-500" : "bg-emerald-500";
  return (
    <div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-200" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
        <div className={`h-full ${bar}`} style={{ width: `${pct}%` }} />
      </div>
      <div className="mt-1.5 flex items-center justify-between text-xs text-slate-500">
        <span>{inr(spend)} / {inr(limit)} ({(limit ? (spend / limit) * 100 : 0).toFixed(1)}%)</span>
        <LimitBadge status={status} />
      </div>
    </div>
  );
}

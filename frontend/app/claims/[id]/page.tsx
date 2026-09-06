"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, useMe } from "@/lib/auth";
import { inr, fmtDate, fmtDateTime } from "@/lib/format";
import { ErrorBox, Shell, StatusBadge } from "@/components/ui";

const ACTION_LABELS: Record<string, string> = {
  CLAIM_CREATED: "Claim created",
  CLAIM_UPDATED: "Claim updated",
  CLAIM_SUBMITTED: "Claim submitted",
  CLAIM_APPROVED: "Claim approved",
  CLAIM_REJECTED: "Claim rejected",
  CLAIM_PAID: "Claim paid",
  DUPLICATE_WARNING: "Duplicate warning",
  PAYMENT_BLOCKED: "Payment blocked",
};

export default function ClaimDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { me } = useMe();
  const router = useRouter();
  const [claim, setClaim] = useState<any>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");

  const load = useCallback(() => {
    api(`/claims/${id}`).then(setClaim).catch((e) => setError(e.message));
  }, [id]);
  useEffect(load, [load]);

  async function act(action: string, body?: any) {
    setBusy(action); setError("");
    try {
      await api(`/claims/${id}/${action}`, { method: "POST", body });
      setRejecting(false);
      load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  if (error && !claim) return <Shell><p className="text-red-600">{error}</p></Shell>;
  if (!claim) return <Shell><p className="text-gray-500">Loading...</p></Shell>;

  const isOwner = me && claim.user_id === me.id;
  const isManager = me?.role === "MANAGER";
  const isFinance = me?.role === "FINANCE";

  return (
    <Shell>
      <div className="flex justify-between items-start mb-4">
        <div>
          <h1 className="text-xl font-bold">{claim.claim_number}</h1>
          <p className="text-sm text-gray-500">{claim.user_name}</p>
        </div>
        <StatusBadge status={claim.status} />
      </div>

      <ErrorBox message={error} />

      <div className="bg-white rounded-lg shadow p-4 mb-4 grid grid-cols-2 gap-3 text-sm">
        <div><p className="text-gray-500">Merchant</p><p className="font-medium">{claim.merchant}</p></div>
        <div><p className="text-gray-500">Date</p><p className="font-medium">{fmtDate(claim.transaction_date)}</p></div>
        <div><p className="text-gray-500">Category</p><p className="font-medium">{claim.category}</p></div>
        <div><p className="text-gray-500">Amount</p><p className="font-medium">{inr(claim.amount)}</p></div>
        <div className="col-span-2"><p className="text-gray-500">Description</p><p className="font-medium">{claim.description || "—"}</p></div>
        {claim.rejection_reason && (
          <div className="col-span-2 text-red-700">
            <p className="text-gray-500">Rejection reason</p><p>{claim.rejection_reason}</p>
          </div>
        )}
        {claim.receipt?.original_text && (
          <div className="col-span-2">
            <p className="text-gray-500 mb-1">Original receipt</p>
            <pre className="bg-gray-50 border rounded p-2 text-xs whitespace-pre-wrap">{claim.receipt.original_text}</pre>
          </div>
        )}
      </div>

      {claim.duplicate_matches?.length > 0 && (
        <div className="bg-amber-50 border border-amber-300 rounded-lg p-4 mb-4 text-sm">
          <p className="font-semibold">⚠ Similar claims exist</p>
          <ul className="mt-1 space-y-1">
            {claim.duplicate_matches.map((m: any) => (
              <li key={m.claim_id}>
                <Link href={`/claims/${m.claim_id}`} className="text-blue-600 hover:underline">
                  {m.claim_number} — {m.merchant} — {inr(m.amount)} — {m.date} ({m.user_name}) — {m.score}%
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap gap-2 mb-6">
        {isOwner && claim.status === "DRAFT" && (
          <>
            <button onClick={() => act("submit")} disabled={!!busy}
              className="bg-blue-600 text-white rounded px-4 py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50">
              {busy === "submit" ? "Submitting..." : "Submit Claim"}
            </button>
            <Link href={`/claims/${id}/edit`}
              className="border rounded px-4 py-2 text-sm font-medium hover:bg-gray-100">Edit</Link>
          </>
        )}
        {isOwner && claim.status === "REJECTED" && (
          <button onClick={() => act("return-to-draft")} disabled={!!busy}
            className="border rounded px-4 py-2 text-sm font-medium hover:bg-gray-100 disabled:opacity-50">
            {busy === "return-to-draft" ? "Saving..." : "Edit & Resubmit (back to draft)"}
          </button>
        )}
        {isManager && claim.status === "SUBMITTED" && (
          <>
            <button onClick={() => act("approve")} disabled={!!busy}
              className="bg-emerald-600 text-white rounded px-4 py-2 text-sm font-medium hover:bg-emerald-700 disabled:opacity-50">
              {busy === "approve" ? "Approving..." : "Approve"}
            </button>
            <button onClick={() => setRejecting(!rejecting)} disabled={!!busy}
              className="bg-red-600 text-white rounded px-4 py-2 text-sm font-medium hover:bg-red-700 disabled:opacity-50">
              Reject
            </button>
          </>
        )}
        {isFinance && claim.status === "APPROVED" && (
          <button onClick={() => act("pay")} disabled={!!busy}
            className="bg-violet-600 text-white rounded px-4 py-2 text-sm font-medium hover:bg-violet-700 disabled:opacity-50">
            {busy === "pay" ? "Paying..." : "Mark as Paid"}
          </button>
        )}
        {claim.status === "PAID" && <p className="text-sm text-violet-700 font-medium">✓ Paid and final — no further changes possible.</p>}
      </div>

      {rejecting && (
        <div className="bg-white border rounded-lg shadow p-4 mb-6 max-w-md space-y-2">
          <label htmlFor="reason" className="text-sm font-medium">Rejection reason</label>
          <input id="reason" value={reason} onChange={(e) => setReason(e.target.value)}
            className="w-full border rounded px-3 py-2" />
          <button onClick={() => act("reject", { reason })} disabled={busy === "reject"}
            className="bg-red-600 text-white rounded px-4 py-1.5 text-sm font-medium disabled:opacity-50">
            {busy === "reject" ? "Rejecting..." : "Confirm rejection"}
          </button>
        </div>
      )}

      <h2 className="text-sm font-semibold text-gray-500 uppercase mb-2">History</h2>
      <ol className="bg-white rounded-lg shadow p-4 space-y-3 text-sm">
        {claim.audit?.map((a: any, i: number) => (
          <li key={i} className="border-l-2 border-gray-200 pl-3">
            <p className="font-medium">{ACTION_LABELS[a.action] || a.action}</p>
            <p className="text-xs text-gray-500">{fmtDateTime(a.created_at)} · {a.user_name || "system"}</p>
            {a.action === "CLAIM_REJECTED" && a.meta?.reason && <p className="text-xs text-red-600">{a.meta.reason}</p>}
          </li>
        ))}
      </ol>
    </Shell>
  );
}

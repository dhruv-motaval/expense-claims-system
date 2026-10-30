"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, getToken, useMe } from "@/lib/auth";
import { inr, fmtDate, fmtDateTime } from "@/lib/format";
import { ErrorBox, Shell, StatusBadge, TierBadge } from "@/components/ui";

const ACTION_LABELS: Record<string, string> = {
  CLAIM_CREATED: "Claim created",
  CLAIM_UPDATED: "Claim updated",
  CLAIM_SUBMITTED: "Claim submitted",
  CLAIM_APPROVED: "Claim approved",
  CLAIM_REJECTED: "Claim rejected",
  CLAIM_PAID: "Claim paid",
  CLAIM_SENT_BACK: "Returned to staff by manager",
  CLAIM_RETURNED_TO_MANAGER: "Returned to manager by finance",
  CLAIM_EDIT_BLOCKED: "Edit attempt refused",
  RECEIPT_FILE_ACCESSED: "Receipt file viewed",
  DUPLICATE_WARNING: "Duplicate warning",
  PAYMENT_BLOCKED: "Payment blocked",
  DUPLICATE_DISMISSED: "Duplicate dismissed (justified)",
};

/** One row of the anchor comparison panel: this claim vs the matched paid one. */
function AnchorRow({ name, cell }: { name: string; cell: { this: any; other: any; equal: boolean } }) {
  const na = (v: any) => (v === null || v === undefined) ? "—" : String(v);
  const mark = na(cell.this) === "—" || na(cell.other) === "—"
    ? "absent" : cell.equal ? "match" : "mismatch";
  const markCls = mark === "match" ? "bg-emerald-100 text-emerald-800"
    : mark === "mismatch" ? "bg-red-100 text-red-700" : "bg-gray-100 text-gray-500";
  return (
    <tr className="border-b last:border-0">
      <td className="px-2 py-1.5 text-xs text-gray-500 uppercase">{name}</td>
      <td className="px-2 py-1.5 font-mono text-xs">{na(cell.this)}</td>
      <td className="px-2 py-1.5 font-mono text-xs">{na(cell.other)}</td>
      <td className="px-2 py-1.5">
        <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${markCls}`}>{mark}</span>
      </td>
    </tr>
  );
}

/** Anchor comparison panel: side-by-side cards fed by the payment-block ticket. */
function AnchorPanel({ compared, matchedNumber }: { compared: Record<string, any> | null; matchedNumber?: string | null }) {
  if (!compared) return null;
  return (
    <div className="bg-white border rounded-lg p-4 mb-4 text-sm">
      <p className="font-semibold mb-2">
        Receipt anchors · this claim {matchedNumber ? `vs paid ${matchedNumber}` : "vs the paid claim it matches"}
      </p>
      <table className="w-full">
        <thead>
          <tr className="text-left text-[10px] text-gray-400 uppercase border-b">
            <th className="px-2 py-1">Field</th><th className="px-2 py-1">This claim</th>
            <th className="px-2 py-1">Paid claim</th><th className="px-2 py-1">Check</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(compared).map(([k, v]) => (
            <AnchorRow key={k} name={k.replace(/_/g, " ")} cell={v as any} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Reviewer feedback card: the latest active send-back / return-to-manager ticket. */
function FeedbackCard({ feedback }: { feedback: any }) {
  if (!feedback) return null;
  const fb = feedback.meta?.feedback || {};
  const fromFinance = feedback.action === "CLAIM_RETURNED_TO_MANAGER";
  const actor = feedback.meta?.actor;
  return (
    <div className={`border rounded-lg p-4 mb-4 text-sm ${fromFinance ? "bg-amber-50 border-amber-300" : "bg-orange-50 border-orange-300"}`}>
      <p className="font-semibold">
        {fromFinance ? "↩ Finance returned this claim to your manager" : "↩ Manager returned this claim to you for revision"}
      </p>
      <ul className="mt-2 space-y-1">
        <li><span className="text-gray-500 text-xs uppercase">Reason</span><p>{fb.reason}</p></li>
        {fb.flagged_fields?.length > 0 && (
          <li>
            <span className="text-gray-500 text-xs uppercase">Fields to fix</span>
            <p className="flex flex-wrap gap-1 mt-1">
              {(fb.flagged_fields as string[]).map((f) => (
                <span key={f} className="px-2 py-0.5 rounded bg-white border font-mono text-xs">{f}</span>
              ))}
            </p>
          </li>
        )}
        {fb.comment && <li><span className="text-gray-500 text-xs uppercase">Comment</span><p>{fb.comment}</p></li>}
      </ul>
      <p className="text-xs text-gray-500 mt-2">
        {actor ? `${actor.name} (${actor.role}) · ` : ""}{fmtDateTime(feedback.created_at)}
        {fromFinance && fb.assigned_to ? " · waiting for your manager to re-review" : ""}
      </p>
    </div>
  );
}

/** Only show feedback that still requires action. A historical return ticket
 * must disappear after the claim is resubmitted, re-approved, or paid. */
function activeFeedback(claim: any) {
  const feedback = claim.latest_feedback;
  if (!feedback) return null;
  if (claim.status === "SENT_BACK") {
    return feedback.action === "CLAIM_SENT_BACK" ? feedback : null;
  }
  if (claim.status !== "SUBMITTED" || feedback.action !== "CLAIM_RETURNED_TO_MANAGER") {
    return null;
  }
  const feedbackAt = new Date(feedback.created_at).getTime();
  const superseded = (claim.audit || []).some((event: any) => {
    const eventAt = new Date(event.created_at).getTime();
    return eventAt > feedbackAt && ["CLAIM_SUBMITTED", "CLAIM_APPROVED", "CLAIM_PAID"].includes(event.action);
  });
  return superseded ? null : feedback;
}

/** Extract the anchor comparison captured in the latest duplicate-related ticket. */
function latestCompared(audit: any[]): { compared: any; matched: string | null; action: string } | null {
  const hit = [...(audit || [])].reverse().find(
    (a) => typeof a.meta?.compared !== "undefined" && a.meta?.compared !== null);
  return hit ? { compared: hit.meta.compared, matched: hit.meta?.matched?.claim ?? null, action: hit.action } : null;
}

export default function ClaimDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { me } = useMe();
  const router = useRouter();
  const [claim, setClaim] = useState<any>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [sendingBack, setSendingBack] = useState(false);   // manager: send back to staff
  const [sbFlagged, setSbFlagged] = useState<string[]>([]);
  const [returning, setReturning] = useState(false);       // finance: return to manager
  const [pageUrls, setPageUrls] = useState<string[]>([]);
  const [previewKind, setPreviewKind] = useState<"" | "pdf" | "image">("");
  const [originalUrl, setOriginalUrl] = useState("");
  const [payJust, setPayJust] = useState(false);      // tier-C justification modal
  const [justification, setJustification] = useState("");
  const objectUrlsRef = useRef<string[]>([]);          // blob URLs to revoke

  const revokeAll = () => {
    objectUrlsRef.current.forEach((u) => URL.revokeObjectURL(u));
    objectUrlsRef.current = [];
  };
  useEffect(() => revokeAll, []);                      // unmount cleanup

  const load = useCallback(() => {
    revokeAll();
    setPageUrls([]); setOriginalUrl(""); 
    api(`/claims/${id}`).then(async (c: any) => {
      setClaim(c);
      try {
        // Receipt preview: PDFs are shown via the authorized original-PDF
        // endpoint (browser-native PDF viewer); images via the same endpoint.
        // Legacy receipts with persisted page images fall back to page previews.
        const rec = c.receipt;
        if (!rec || (!rec.s3_image_key && !rec.image_path)) return;
        const token = getToken() || "";
        if (rec.has_pages) {
          try {
            const d = await api(`/receipts/pages/${id}`);
            const urls: string[] = [];
            for (const p of d.pages || []) {
              const isPresigned = p.url.startsWith("http");
              const res = await fetch(p.url, isPresigned ? {} : { headers: { Authorization: `Bearer ${token}` } });
              if (res.ok) {
                const u = URL.createObjectURL(await res.blob());
                objectUrlsRef.current.push(u);
                urls.push(u);
              }
            }
            if (urls.length) { setPageUrls(urls); return; }
          } catch { /* fall through to original-file preview */ }
        }
        const res = await fetch(`/api/receipts/image/${id}`, { headers: { Authorization: `Bearer ${token}` } });
        if (res.ok) {
          const blob = await res.blob();
          const url = URL.createObjectURL(blob);
          objectUrlsRef.current.push(url);
          if (rec.content_type === "application/pdf" || rec.s3_image_key?.endsWith(".pdf")) {
            setPreviewKind("pdf");
          } else {
            setPreviewKind("image");
          }
          setOriginalUrl(url);
        }
      } catch {
        /* preview is best-effort */
      }
    }).catch((e) => setError(e.message));
  }, [id]);
  useEffect(load, [load]);

  async function act(action: string, body?: any) {
    setBusy(action); setError("");
    try {
      await api(`/claims/${id}/${action}`, { method: "POST", body });
      setRejecting(false);
      setPayJust(false);
      load();
    } catch (e: any) {
      setError(e.message);
      // The server's tier-C gate asks for a justification: open the modal instead
      // of leaving the user with a bare error.
      if (action === "pay" && /justification/.test(e.message)) setPayJust(true);
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
        <div className="flex items-center gap-2">
          <StatusBadge status={claim.status} />
          <TierBadge tier={claim.duplicate_tier} />
        </div>
      </div>

      <ErrorBox message={error} />

      <FeedbackCard feedback={activeFeedback(claim)} />

      {/* receipt tier chip: where this file physically lives */}
      {claim.receipt?.storage_tier && (
        <p className="text-xs text-gray-500 mb-2">
          Receipt storage: <span className={`px-1.5 py-0.5 rounded font-semibold ${claim.receipt.storage_tier === "HOT" ? "bg-blue-100 text-blue-800" : "bg-gray-200 text-gray-700"}`}>
            {claim.receipt.storage_tier === "HOT" ? "Hot (active)" : "Cold (archived)"}
          </span>
        </p>
      )}

      <AnchorPanel
        compared={latestCompared(claim.audit)?.compared ?? null}
        matchedNumber={latestCompared(claim.audit)?.matched ?? null}
      />

      <div className="bg-white rounded-lg shadow p-4 mb-4 grid grid-cols-2 gap-3 text-sm">
        <div><p className="text-gray-500">Merchant</p><p className="font-medium">{claim.merchant}</p></div>
        <div><p className="text-gray-500">Date</p><p className="font-medium">{fmtDate(claim.transaction_date)}</p></div>
        {claim.trans_time && (
          <div><p className="text-gray-500">Time</p><p className="font-mono font-medium">{claim.trans_time}</p></div>
        )}
        {claim.receipt_no && (
          <div><p className="text-gray-500">Receipt No.</p><p className="font-mono font-medium">{claim.receipt_no}</p></div>
        )}
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
            <p className="text-gray-500 mb-1">Original receipt text</p>
            <pre className="bg-gray-50 border rounded p-2 text-xs whitespace-pre-wrap">{claim.receipt.original_text}</pre>
          </div>
        )}
        {previewKind === "pdf" && originalUrl && (
          <div className="col-span-2">
            <p className="text-gray-500 mb-1">Original receipt (PDF preview)</p>
            {/* sandboxed: the blob renders, but nothing in it can execute */}
            <iframe src={originalUrl} title="Receipt PDF preview" sandbox="allow-same-origin"
              className="w-full h-96 rounded border bg-gray-50" />
            <a href={originalUrl} download={claim.receipt?.original_filename || "receipt.pdf"}
              className="text-xs text-blue-600 hover:underline">Download original PDF</a>
          </div>
        )}
        {previewKind === "image" && originalUrl && (
          <div className="col-span-2">
            <p className="text-gray-500 mb-1">Original receipt</p>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={originalUrl} alt="Receipt" className="max-h-72 rounded border" />
          </div>
        )}
        {pageUrls.length > 0 && (
          <div className="col-span-2">
            <p className="text-gray-500 mb-1">Receipt pages</p>
            <div className="flex flex-wrap gap-2">
              {pageUrls.map((u, i) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img key={i} src={u} alt={`Receipt page ${i + 1}`}
                  className="h-40 w-auto rounded border" />
              ))}
            </div>
          </div>
        )}
        {claim.receipt?.extracted_data && (
          <div className="col-span-2">
            <p className="text-gray-500 mb-1">Extracted data</p>
            <div className="grid grid-cols-2 gap-2 text-xs">
              {Object.entries(claim.receipt.extracted_data)
                .filter(([, v]) => v !== null && v !== "")
                .map(([k, v]) => (
                  <div key={k}><span className="text-gray-400">{k}</span>: {String(v)}</div>
                ))}
            </div>
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
        {isOwner && ["DRAFT", "SENT_BACK"].includes(claim.status) && (
          <>
            <button onClick={() => act("submit")} disabled={!!busy}
              className="bg-blue-600 text-white rounded px-4 py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50">
              {busy === "submit" ? "Submitting..." : claim.status === "SENT_BACK" ? "Resubmit Claim" : "Submit Claim"}
            </button>
            <Link href={`/claims/${id}/edit`}
              className="border rounded px-4 py-2 text-sm font-medium hover:bg-gray-100">Edit</Link>
          </>
        )}
        {isOwner && claim.status === "SENT_BACK" && (
          <button onClick={() => act("return-to-draft")} disabled={!!busy}
            className="border rounded px-4 py-2 text-sm font-medium hover:bg-gray-100 disabled:opacity-50"
            title="Keep it as a private draft for longer">
            {busy === "return-to-draft" ? "Saving..." : "Move back to draft (finish later)"}
          </button>
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
            <button onClick={() => setSendingBack(!sendingBack)} disabled={!!busy}
              className="bg-orange-600 text-white rounded px-4 py-2 text-sm font-medium hover:bg-orange-700 disabled:opacity-50"
              title="Send this claim back to the employee so they can fix it and resubmit">
              Send back for revision
            </button>
          </>
        )}
        {isManager && claim.status === "APPROVED" && (
          <p className="text-sm text-emerald-700 font-medium">
            Approved — now with Finance. Payments go through Finance; you'll be notified
            if Finance sends it back to you.</p>
        )}
        {isFinance && claim.status === "APPROVED" && (
          <>
            <button onClick={() => act("pay")} disabled={!!busy}
              className="bg-violet-600 text-white rounded px-4 py-2 text-sm font-medium hover:bg-violet-700 disabled:opacity-50">
              {busy === "pay" ? "Paying..." : claim.duplicate_tier === "C" ? "Pay anyway (justify)…" : "Mark as Paid"}
            </button>
          </>
        )}
        {isFinance && claim.status === "SUBMITTED" && (
          <>
            <button onClick={() => setReturning(!returning)} disabled={!!busy}
              className="bg-amber-600 text-white rounded px-4 py-2 text-sm font-medium hover:bg-amber-700 disabled:opacity-50"
              title="Send this approved claim back to its manager with your feedback">
              Return to manager
            </button>
          </>
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

      {sendingBack && (
        <div className="bg-white border rounded-lg shadow p-4 mb-6 max-w-md space-y-2">
          <label htmlFor="sb-reason" className="text-sm font-medium">Why is this being sent back?</label>
          <input id="sb-reason" value={reason} onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. Amount does not match the receipt"
            className="w-full border rounded px-3 py-2" />
          <div className="flex flex-wrap gap-1">
            {["merchant", "transaction_date", "trans_time", "receipt_no", "category", "amount", "description"].map((f) => (
              <button key={f} type="button"
                onClick={() => setSbFlagged(sbFlagged.includes(f) ? sbFlagged.filter((x) => x !== f) : [...sbFlagged, f])}
                className={`px-2 py-0.5 rounded text-xs border font-mono ${sbFlagged.includes(f) ? "bg-orange-100 border-orange-400 text-orange-800" : "bg-white"}`}>
                {f}
              </button>
            ))}
          </div>
          <button onClick={() => act("send-back", { reason, flagged_fields: sbFlagged })}
            disabled={busy === "send-back" || !reason.trim()}
            className="bg-orange-600 text-white rounded px-4 py-1.5 text-sm font-medium disabled:opacity-50">
            {busy === "send-back" ? "Sending back..." : "Confirm send-back"}
          </button>
          <p className="text-xs text-gray-500">The employee can then edit and resubmit; you approve the revision.</p>
        </div>
      )}

      {returning && (
        <div className="bg-white border rounded-lg shadow p-4 mb-6 max-w-md space-y-2">
          <label htmlFor="ret-reason" className="text-sm font-medium">
            Feedback for the manager (finance never contacts staff directly)
          </label>
          <input id="ret-reason" value={reason} onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. Category mismatch: receipt shows accommodation"
            className="w-full border rounded px-3 py-2" />
          <button onClick={() => act("return-to-manager", { reason })} disabled={busy === "return-to-manager" || !reason.trim()}
            className="bg-amber-600 text-white rounded px-4 py-1.5 text-sm font-medium disabled:opacity-50">
            {busy === "return-to-manager" ? "Returning..." : "Confirm return to manager"}
          </button>
        </div>
      )}

      {payJust && (
        <div className="bg-amber-50 border border-amber-300 rounded-lg shadow p-4 mb-6 max-w-md space-y-2">
          <p className="text-sm font-semibold">Duplicate check (tier C)</p>
          <p className="text-xs text-gray-600">
            Same merchant, date and amount were already paid. Confirm this claim is a
            genuine separate transaction. A typed justification is stored on the audit trail.
          </p>
          <textarea rows={3} value={justification} onChange={(e) => setJustification(e.target.value)}
            placeholder="Min 20 characters — e.g. confirmed with the vendor this is a second trip the same day"
            className="w-full border rounded px-3 py-2 text-sm" />
          <button onClick={() => act("pay", { justification })} disabled={busy === "pay" || justification.trim().length < 20}
            className="bg-violet-600 text-white rounded px-4 py-1.5 text-sm font-medium disabled:opacity-50">
            {busy === "pay" ? "Paying..." : "Pay with justification"}
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
            {a.action === "CLAIM_SENT_BACK" && a.meta?.feedback && (
              <p className="text-xs text-orange-700">{a.meta.feedback.reason}</p>
            )}
            {a.action === "CLAIM_RETURNED_TO_MANAGER" && a.meta?.feedback && (
              <p className="text-xs text-amber-700">{a.meta.feedback.reason}</p>
            )}
            {(a.action === "PAYMENT_BLOCKED" || a.action === "DUPLICATE_DISMISSED") && a.meta?.compared && (
              <details className="text-xs mt-1">
                <summary className="cursor-pointer text-gray-600">
                  tier {a.meta.tier} {a.action === "DUPLICATE_DISMISSED" && "· justified"} — anchor comparison
                </summary>
                <pre className="bg-gray-50 border rounded p-2 mt-1 overflow-x-auto whitespace-pre-wrap">
                  {JSON.stringify(a.meta, null, 2)}
                </pre>
              </details>
            )}
            {a.action === "CLAIM_UPDATED" && a.meta?.fields && (
              <details className="text-xs mt-1">
                <summary className="cursor-pointer text-gray-600">field changes</summary>
                <pre className="bg-gray-50 border rounded p-2 mt-1 overflow-x-auto whitespace-pre-wrap">
                  {JSON.stringify(a.meta.fields, null, 2)}
                </pre>
              </details>
            )}
            {a.action === "RECEIPT_FILE_ACCESSED" && (
              <p className="text-xs text-gray-600">
                {a.meta?.filename} {(a.meta?.served_from || a.meta?.tier) ? `· served from ${a.meta?.served_from || a.meta?.tier}` : ""}
              </p>
            )}
            {a.action === "CLAIM_EDIT_BLOCKED" && (
              <p className="text-xs text-red-600">attempted {a.meta?.attempted_fields?.join(", ")} on a {a.meta?.status} claim</p>
            )}
          </li>
        ))}
      </ol>
    </Shell>
  );
}

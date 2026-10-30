"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/auth";
import { ErrorBox, Shell } from "@/components/ui";
import { inr } from "@/lib/format";

const CATEGORIES = ["TRAVEL", "MEALS", "SUPPLIES", "TAXI", "ACCOMMODATION", "OTHER"];

interface Match {
  claim_id: number; claim_number: string; user_name: string; merchant: string;
  amount: number; date: string; score: number; level: string;
}

export default function NewClaimPage() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [form, setForm] = useState<any>(null);
  const [matches, setMatches] = useState<Match[]>([]);
  const [override, setOverride] = useState(false);
  const [receiptId, setReceiptId] = useState<number | null>(null);

  useEffect(() => {
    if (!file) { setPreview(""); return; }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  async function extractText() {
    setBusy("extract"); setError(""); setMatches([]); setOverride(false);
    try {
      const body = new FormData();
      body.append("text", text);
      const d = await api("/receipts/extract", { method: "POST", body });
      setForm({
        merchant: d.merchant || "", transaction_date: d.date || "",
        category: d.category || "OTHER", amount: d.amount ?? "",
        currency: d.currency || "INR", description: d.description || "",
        receipt_no: d.receipt_no || "", trans_time: d.time || "",
      });
      setMatches(d.duplicate_matches || []);
      setReceiptId(d.receipt_id);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  async function uploadImage() {
    if (!file) return;
    setBusy("extract"); setError(""); setMatches([]); setOverride(false);
    try {
      const body = new FormData();
      body.append("file", file);
      const d = await api("/receipts/upload", { method: "POST", body });
      setForm({
        merchant: d.merchant || "", transaction_date: d.date || "",
        category: d.category || "OTHER", amount: d.amount ?? "",
        currency: d.currency || "INR", description: d.description || "",
        receipt_no: d.receipt_no || "", trans_time: d.time || "",
      });
      setMatches(d.duplicate_matches || []);
      setReceiptId(d.receipt_id);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  async function save(submit: boolean) {
    setBusy(submit ? "submit" : "draft"); setError("");
    try {
      const d = await api("/claims", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...form, amount: Number(form.amount), receipt_id: receiptId }),
      });
      if (submit) await api(`/claims/${d.id}/submit`, { method: "POST" });
      router.push(`/claims/${d.id}`);
    } catch (e: any) {
      setError(e.message);
      setBusy("");
    }
  }

  const strong = matches.some((m) => m.level === "strong");

  return (
    <Shell>
      <div className="mb-6">
        <p className="text-xs font-bold uppercase tracking-widest text-blue-600">Expense submission</p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-900">Create a new claim</h1>
        <p className="mt-1 text-sm text-slate-500">Start with a receipt. You can review and correct the extracted details before saving.</p>
      </div>

      {!form && (
        <div className="space-y-4 max-w-xl">
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-3">
            <div className="flex items-start gap-3">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-sm font-bold text-slate-700">1</span>
              <div>
                <h2 className="font-semibold text-slate-900">Paste receipt text</h2>
                <p className="mt-0.5 text-sm text-slate-500">Or enter the receipt text manually if you don’t have a file.</p>
              </div>
            </div>
            <label htmlFor="receipt-text" className="sr-only">Paste receipt text</label>
            <textarea
              id="receipt-text" rows={5} value={text} onChange={(e) => setText(e.target.value)}
              placeholder={"UBER\n12 Aug 2026\nTrip to Airport\n₹320"}
              className="w-full border rounded px-3 py-2 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <button
              onClick={extractText} disabled={busy === "extract" || !text.trim()}
              className="bg-blue-600 text-white rounded px-4 py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
            >
              {busy === "extract" ? "Extracting..." : "Extract Claim"}
            </button>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-4">
            <div className="flex items-start gap-3">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-100 text-sm font-bold text-blue-700">2</span>
              <div>
                <h2 className="font-semibold text-slate-900">Upload a receipt</h2>
                <p className="mt-0.5 text-sm text-slate-500">Upload a PDF or image and we’ll fill in the claim details for you.</p>
              </div>
            </div>
            <input
              ref={fileInputRef}
              id="receipt-file" type="file" accept="application/pdf,image/jpeg,image/png,image/webp,.pdf,.jpg,.jpeg,.png,.webp"
              onChange={(e) => {
                const f = e.target.files?.[0] || null;
                setError("");
                if (f) {
                  const okType = f.type === "application/pdf" || ["image/jpeg", "image/png", "image/webp"].includes(f.type)
                    || /\.(pdf|jpe?g|png|webp)$/i.test(f.name);
                  if (!okType) { setError("Unsupported file. Upload a PDF, JPG, PNG or WebP receipt."); return; }
                  if (f.size > 10 * 1024 * 1024) { setError("File too large. Max size is 10 MB."); return; }
                  if (f.size === 0) { setError("The selected file is empty."); return; }
                }
                setFile(f);
              }}
              className="sr-only"
            />
            {!file && (
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="group w-full rounded-xl border-2 border-dashed border-slate-300 bg-slate-50 px-4 py-7 text-center transition hover:border-blue-400 hover:bg-blue-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <span className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-white text-2xl shadow-sm">↑</span>
                <span className="mt-3 block text-sm font-semibold text-slate-800">Choose receipt file</span>
                <span className="mt-1 block text-xs text-slate-500">PDF, JPG, PNG or WebP · maximum 10 MB</span>
              </button>
            )}
            {file && (
              <div className="flex items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 p-3">
                {preview && !/\.pdf$/i.test(file.name) && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={preview} alt="Receipt preview" className="h-14 w-14 object-cover rounded border border-white" />
                )}
                {!preview || /\.pdf$/i.test(file.name) ? <span className="flex h-14 w-14 items-center justify-center rounded border bg-white text-xs font-bold text-red-600">PDF</span> : null}
                <div className="min-w-0 flex-1 text-sm">
                  <p className="truncate font-semibold text-slate-800">{file.name}</p>
                  <p className="text-xs text-slate-500">{(file.size / 1024).toFixed(0)} KB · Ready to process</p>
                </div>
                <button
                  type="button"
                  onClick={() => { setFile(null); setPreview(""); if (fileInputRef.current) fileInputRef.current.value = ""; }}
                  disabled={busy === "extract"}
                  className="rounded-lg px-2 py-1 text-sm font-medium text-red-600 hover:bg-red-100 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-red-400"
                >
                  Remove
                </button>
              </div>
            )}
            <button
              type="button"
              onClick={uploadImage} disabled={busy === "extract" || !file}
              className="w-full rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {busy === "extract"
                ? (file && (file.type === "application/pdf" || /\.pdf$/i.test(file.name))
                  ? "Processing PDF (extracting text)..." : "Uploading & extracting...")
                : "Upload & Extract Receipt"}
            </button>
          </div>
          <ErrorBox message={error} />
        </div>
      )}

      {form && (
        <div className="max-w-xl space-y-4">
          {matches.length > 0 && !override && (
            <div className={`rounded-lg border p-4 ${strong ? "bg-red-50 border-red-300" : "bg-amber-50 border-amber-300"}`}>
              <p className="font-semibold text-sm">Possible duplicate receipt</p>
              <p className="text-xs text-gray-600 mt-1">We found existing claims that look similar:</p>
              <ul className="mt-2 space-y-1 text-sm">
                {matches.slice(0, 3).map((m) => (
                  <li key={m.claim_id} className="flex justify-between">
                    <Link href={`/claims/${m.claim_id}`} className="text-blue-600 hover:underline">
                      {m.claim_number} — {m.merchant} — {inr(m.amount)} — {m.date} ({m.user_name})
                    </Link>
                    <span className={m.level === "strong" ? "text-red-700 font-semibold" : "text-amber-700"}>
                      {m.score}% {m.level}
                    </span>
                  </li>
                ))}
              </ul>
              <div className="mt-3 flex gap-2">
                <button
                  onClick={() => setOverride(true)}
                  className="bg-gray-800 text-white rounded px-3 py-1.5 text-xs font-medium hover:bg-gray-900"
                >
                  Continue Anyway
                </button>
                <button
                  onClick={() => setForm(null)}
                  className="border rounded px-3 py-1.5 text-xs font-medium hover:bg-gray-100"
                >
                  Discard
                </button>
              </div>
            </div>
          )}

          <div className="bg-white rounded-lg shadow p-4 space-y-3">
            <p className="text-xs font-semibold text-gray-500 uppercase">Review & edit extracted claim</p>
            <div className="grid grid-cols-2 gap-3">
              <label className="text-sm">
                <span className="block font-medium mb-1">Merchant</span>
                <input value={form.merchant} onChange={(e) => setForm({ ...form, merchant: e.target.value })}
                  className="w-full border rounded px-3 py-2" required />
              </label>
              <label className="text-sm">
                <span className="block font-medium mb-1">Date</span>
                <input type="date" value={form.transaction_date}
                  onChange={(e) => setForm({ ...form, transaction_date: e.target.value })}
                  className="w-full border rounded px-3 py-2" required />
              </label>
              <label className="text-sm">
                <span className="block font-medium mb-1">Category</span>
                <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}
                  className="w-full border rounded px-3 py-2">
                  {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
                </select>
              </label>
              <label className="text-sm">
                <span className="block font-medium mb-1">Amount (₹)</span>
                <input type="number" min="0.01" step="0.01" value={form.amount}
                  onChange={(e) => setForm({ ...form, amount: e.target.value })}
                  className="w-full border rounded px-3 py-2" required />
              </label>
              <label className="text-sm">
                <span className="block font-medium mb-1">
                  Receipt No.
                  <span className="block text-xs font-normal text-gray-500">Duplicate checks match on this — type it exactly as printed</span>
                </span>
                <input value={form.receipt_no || ""} onChange={(e) => setForm({ ...form, receipt_no: e.target.value })}
                  placeholder="e.g. UB984213X" className="w-full border rounded px-3 py-2 font-mono font-medium" />
              </label>
              <label className="text-sm">
                <span className="block font-medium mb-1">
                  Time
                  <span className="block text-xs font-normal text-gray-500">Transaction time (HH:MM or like 2:30 pm)</span>
                </span>
                <input value={form.trans_time || ""} onChange={(e) => setForm({ ...form, trans_time: e.target.value })}
                  placeholder="e.g. 14:22" className="w-full border rounded px-3 py-2 font-mono font-medium" />
              </label>
            </div>
            <label className="text-sm block">
              <span className="block font-medium mb-1">Description</span>
              <input value={form.description || ""} onChange={(e) => setForm({ ...form, description: e.target.value })}
                className="w-full border rounded px-3 py-2" />
            </label>
            <ErrorBox message={error} />
            <div className="flex gap-2 pt-1">
              <button
                onClick={() => save(false)} disabled={!!busy}
                className="border rounded px-4 py-2 text-sm font-medium hover:bg-gray-100 disabled:opacity-50"
              >
                {busy === "draft" ? "Saving..." : "Save Draft"}
              </button>
              <button
                onClick={() => save(true)} disabled={!!busy || (matches.length > 0 && !override)}
                title={matches.length > 0 && !override ? "Acknowledge the duplicate warning first" : undefined}
                className="bg-blue-600 text-white rounded px-4 py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
              >
                {busy === "submit" ? "Submitting..." : "Submit Claim"}
              </button>
            </div>
          </div>
        </div>
      )}
    </Shell>
  );
}

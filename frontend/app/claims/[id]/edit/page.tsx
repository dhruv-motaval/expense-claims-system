"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/auth";
import { ErrorBox, Shell } from "@/components/ui";

const CATEGORIES = ["TRAVEL", "MEALS", "SUPPLIES", "TAXI", "ACCOMMODATION", "OTHER"];

export default function EditClaimPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [form, setForm] = useState<any>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api(`/claims/${id}`).then((c) =>
      setForm({
        merchant: c.merchant, transaction_date: c.transaction_date, category: c.category,
        amount: c.amount, currency: c.currency, description: c.description || "",
      })
    ).catch((e) => setError(e.message));
  }, [id]);

  async function save() {
    setBusy(true); setError("");
    try {
      await api(`/claims/${id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...form, amount: Number(form.amount) }),
      });
      router.push(`/claims/${id}`);
    } catch (e: any) {
      setError(e.message);
      setBusy(false);
    }
  }

  return (
    <Shell>
      <h1 className="text-xl font-bold mb-4">Edit Claim</h1>
      {form && (
        <div className="bg-white rounded-lg shadow p-4 max-w-xl space-y-3">
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
          </div>
          <label className="text-sm block">
            <span className="block font-medium mb-1">Description</span>
            <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })}
              className="w-full border rounded px-3 py-2" />
          </label>
          <ErrorBox message={error} />
          <button onClick={save} disabled={busy}
            className="bg-blue-600 text-white rounded px-4 py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50">
            {busy ? "Saving..." : "Save"}
          </button>
        </div>
      )}
    </Shell>
  );
}

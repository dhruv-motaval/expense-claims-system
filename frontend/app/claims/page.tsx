"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/auth";
import { Shell } from "@/components/ui";
import ClaimTable from "@/components/claims";

export default function ClaimsPage() {
  const [claims, setClaims] = useState<any[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/claims").then((d) => setClaims(d.claims || d)).catch((e) => setError(e.message));
  }, []);

  return (
    <Shell>
      <h1 className="text-xl font-bold mb-4">Claims</h1>
      {error && <p className="text-red-600">{error}</p>}
      <ClaimTable claims={claims} showEmployee />
      <p className="text-xs text-gray-500 mt-2">
        Showing claims you can access. Open a claim for the receipt, timeline and actions.
      </p>
    </Shell>
  );
}

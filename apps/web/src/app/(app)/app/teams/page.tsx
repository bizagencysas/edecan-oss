"use client";

import { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";

/** `/app/teams` redirige al listado unificado de chats en `/app/bots`. */
export default function TeamsRedirectPage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    const team = searchParams.get("team");
    const target = team ? `/app/bots?team=${encodeURIComponent(team)}` : "/app/bots";
    router.replace(target);
  }, [router, searchParams]);

  return null;
}

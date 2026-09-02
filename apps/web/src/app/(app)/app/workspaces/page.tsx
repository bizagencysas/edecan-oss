"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Workspaces se administra desde la superficie unificada de bots y equipos. */
export default function WorkspacesRedirectPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/app/bots");
  }, [router]);

  return null;
}

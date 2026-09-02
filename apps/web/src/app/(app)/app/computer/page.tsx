"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** La sesión de computadora vive en la superficie remota compartida. */
export default function ComputerRedirectPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/app/remoto");
  }, [router]);

  return null;
}

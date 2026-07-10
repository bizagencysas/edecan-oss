"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { Logo } from "@/components/Logo";

import { NAV_ITEMS } from "./nav-items";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function NavList({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();

  return (
    <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto px-3 py-2 thin-scrollbar">
      {NAV_ITEMS.map((item) => {
        // "/app" (Chat) es prefijo de TODAS las demás rutas (/app/panel,
        // /app/persona, ...), así que necesita match exacto — si no, el
        // ítem de Chat quedaría marcado "activo" en cualquier otra página.
        const active =
          item.href === "/app"
            ? pathname === "/app"
            : pathname === item.href || pathname?.startsWith(`${item.href}/`);
        const Icon = item.icon;
        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            className={cx(
              "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
              active
                ? "bg-brand-50 text-brand-700 dark:bg-brand-900/40 dark:text-brand-300"
                : "text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white",
            )}
          >
            <Icon className="h-4 w-4 shrink-0" />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}

export function BrandMark() {
  return (
    <div className="px-4 py-4">
      <Logo markClassName="h-8 w-8" wordClassName="text-base" />
    </div>
  );
}

export function Sidebar() {
  return (
    <aside className="hidden w-64 shrink-0 flex-col border-r border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 md:flex">
      <BrandMark />
      <NavList />
    </aside>
  );
}

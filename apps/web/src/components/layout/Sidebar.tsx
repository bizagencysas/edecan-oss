"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Logo } from "@/components/Logo";
import { ChevronDownIcon } from "@/components/icons";

import {
  ADVANCED_NAV_GROUPS,
  ADVANCED_NAV_ITEMS,
  isNavItemActive,
  PRIMARY_NAV_ITEMS,
  type NavItem,
} from "./nav-items";

const ADVANCED_MODE_KEY = "edecan:advanced-navigation";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function NavList({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const advancedRouteActive = ADVANCED_NAV_ITEMS.some((item) => isNavItemActive(pathname, item.href));
  const [advancedOpen, setAdvancedOpen] = useState(false);

  useEffect(() => {
    let savedOpen = false;
    try {
      savedOpen = window.localStorage.getItem(ADVANCED_MODE_KEY) === "true";
    } catch {
      // La navegación sigue funcionando si el navegador bloquea storage.
    }
    if (advancedRouteActive || savedOpen) {
      setAdvancedOpen(true);
    }
  }, [advancedRouteActive]);

  function toggleAdvanced() {
    setAdvancedOpen((current) => {
      const next = !current;
      try {
        window.localStorage.setItem(ADVANCED_MODE_KEY, String(next));
      } catch {
        // Mantiene el estado de esta sesión aunque no se pueda persistir.
      }
      return next;
    });
  }

  return (
    <nav aria-label="Navegación principal" className="flex flex-1 flex-col overflow-y-auto px-3 py-2 thin-scrollbar">
      <div className="space-y-0.5">
        {PRIMARY_NAV_ITEMS.map((item) => (
          <NavLink key={item.href} item={item} active={isNavItemActive(pathname, item.href)} onNavigate={onNavigate} />
        ))}
      </div>

      <div className="mt-4 border-t border-slate-100 pt-3 dark:border-slate-800">
        <button
          type="button"
          aria-expanded={advancedOpen}
          aria-controls="advanced-navigation"
          onClick={toggleAdvanced}
          className="flex w-full items-center justify-between rounded-lg px-3 py-2 text-xs font-medium text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
        >
          <span>Modo avanzado</span>
          <ChevronDownIcon className={cx("h-3.5 w-3.5 transition-transform", advancedOpen && "rotate-180")} />
        </button>

        {advancedOpen && (
          <div id="advanced-navigation" className="mt-2 space-y-4 pb-3">
            {ADVANCED_NAV_GROUPS.map((group) => (
              <div key={group.label}>
                <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                  {group.label}
                </p>
                <div className="space-y-0.5">
                  {group.items.map((item) => (
                    <NavLink
                      key={item.href}
                      item={item}
                      active={isNavItemActive(pathname, item.href)}
                      onNavigate={onNavigate}
                      compact
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </nav>
  );
}

function NavLink({
  item,
  active,
  onNavigate,
  compact = false,
}: {
  item: NavItem;
  active: boolean;
  onNavigate?: () => void;
  compact?: boolean;
}) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      onClick={onNavigate}
      className={cx(
        "flex items-center gap-2.5 rounded-lg px-3 font-medium transition-colors",
        compact ? "py-1.5 text-xs" : "py-2 text-sm",
        active
          ? "bg-brand-50 text-brand-700 dark:bg-brand-900/40 dark:text-brand-300"
          : "text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white",
      )}
    >
      <Icon className={cx("shrink-0", compact ? "h-3.5 w-3.5" : "h-4 w-4")} />
      {item.label}
    </Link>
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

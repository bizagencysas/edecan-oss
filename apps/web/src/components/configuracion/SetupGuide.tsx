import type { ReactNode } from "react";

export function OfficialLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 text-xs font-semibold text-brand-600 hover:text-brand-700 hover:underline dark:text-brand-400"
    >
      {children} <span aria-hidden="true">↗</span>
    </a>
  );
}

export function SetupSteps({ children }: { children: ReactNode }) {
  return (
    <ol className="space-y-2 rounded-lg border border-slate-200 bg-white p-3 text-xs leading-5 text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300">
      {children}
    </ol>
  );
}

export function SetupStep({ number, children }: { number: number; children: ReactNode }) {
  return (
    <li className="flex gap-2">
      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-50 text-[11px] font-bold text-brand-700 dark:bg-brand-950 dark:text-brand-300">
        {number}
      </span>
      <span>{children}</span>
    </li>
  );
}

"use client";

// The one navigation bar for every working surface (workspace / casting /
// analytics). It replaces the ad-hoc "Casting →  Timeline →" links each page
// used to grow in its own header, so which surfaces exist — and which one you
// are on — is stated in exactly one place.
//
// The landing page (/) deliberately does NOT mount this: it has its own
// transparent nav floating over the hero, and a solid sticky bar would sit on
// top of that composition.

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { FOCUS_RING } from "@/components/casting/theme";

const SURFACES = [
  { href: "/workspace", label: "Workspace" },
  { href: "/casting", label: "Casting" },
  { href: "/dashboard", label: "Analytics" },
] as const;

export default function AppNav({
  /** Right-hand slot: the API mode badge, judge panels, per-surface controls. */
  children,
  /** Match the surface's own content width. `full` spans the viewport (the
   * workspace, which is an edit bay rather than a document). */
  width = "max-w-7xl",
}: {
  children?: ReactNode;
  width?: string;
}) {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-40 shrink-0 border-b border-[var(--hairline)] bg-[var(--cast-bg)]/85 backdrop-blur-md">
      {/* 54px, fixed: the workspace switches its whole middle column under this
          bar, and a bar that changed height with its contents would make that
          read as a page change. */}
      <div
        className={`mx-auto flex min-h-[54px] w-full items-center gap-2 px-4 py-2 sm:px-6 ${width}`}
      >
        <Link
          href="/"
          className={`shrink-0 rounded-sm text-xs font-semibold tracking-wide text-zinc-200 transition-colors hover:text-zinc-50 ${FOCUS_RING}`}
        >
          Story Engine
        </Link>
        <span className="mx-1 h-4 w-px shrink-0 bg-[var(--hairline)]" aria-hidden />
        <nav aria-label="Primary" className="min-w-0">
          <ul className="flex items-center gap-1">
            {SURFACES.map((s) => {
              const active =
                pathname === s.href || pathname.startsWith(`${s.href}/`);
              return (
                <li key={s.href}>
                  <Link
                    href={s.href}
                    aria-current={active ? "page" : undefined}
                    className={`inline-block rounded-md border px-2.5 py-1 text-xs transition-colors ${
                      active
                        ? "border-amber-500/30 bg-amber-500/[0.09] font-medium text-amber-200"
                        : "border-transparent text-zinc-400 hover:bg-white/[0.04] hover:text-zinc-100"
                    } ${FOCUS_RING}`}
                  >
                    {s.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        {children && (
          <div className="ml-auto flex shrink-0 items-center gap-3">{children}</div>
        )}
      </div>
    </header>
  );
}

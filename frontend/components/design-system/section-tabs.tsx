"use client";

import { Modal } from "antd";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { useAuth } from "@/components/auth-provider";
import { can, type Capability } from "@/lib/api";

export type SectionTab = {
  href: string;
  label: string;
  // Hidden (not disabled) for someone who may not open it; absent when every role may.
  capability?: Capability;
};

export type SectionTabsProps = {
  label: string;
  tabs: readonly SectionTab[];
  // The page has edits that leaving would lose; leaving asks first.
  dirty?: boolean;
};

function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * Tabs that are real routes, so each one keeps its own route protection and its own address.
 * They belong in a page header's `filters`, which keeps the header sticky and full-bleed.
 */
export function SectionTabs({ label, tabs, dirty = false }: SectionTabsProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { session } = useAuth();
  const [pending, setPending] = useState<string | null>(null);
  const visible = tabs.filter((tab) => !tab.capability || can(session, tab.capability));
  // A single tab is not a choice.
  if (visible.length < 2) return null;

  return (
    <>
      <nav className="section-tabs" aria-label={label}>
        {visible.map((tab) => {
          const active = isActive(pathname, tab.href);
          return (
            <Link
              key={tab.href}
              href={tab.href}
              className={`section-tabs__tab${active ? " section-tabs__tab--active" : ""}`}
              aria-current={active ? "page" : undefined}
              onClick={(event) => {
                if (dirty && !active) {
                  event.preventDefault();
                  setPending(tab.href);
                }
              }}
            >
              {tab.label}
            </Link>
          );
        })}
      </nav>
      <Modal
        open={pending !== null}
        title="Descartar cambios sin guardar"
        okText="Descartar cambios"
        cancelText="Seguir editando"
        okButtonProps={{ danger: true }}
        onCancel={() => setPending(null)}
        onOk={() => {
          const href = pending;
          setPending(null);
          if (href) router.push(href);
        }}
      >
        <p>Lo que editaste se perderá si cambiás de pestaña.</p>
      </Modal>
    </>
  );
}

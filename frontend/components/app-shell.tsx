"use client";

import {
  ApiOutlined,
  AuditOutlined,
  BookOutlined,
  ClockCircleOutlined,
  ContactsOutlined,
  DashboardOutlined,
  DownOutlined,
  EnvironmentOutlined,
  LeftOutlined,
  MailOutlined,
  MessageOutlined,
  NotificationOutlined,
  RightOutlined,
  RobotOutlined,
  ShopOutlined,
  StopOutlined,
  TagsOutlined,
  TeamOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { Button, Drawer, Layout, Menu, Tooltip, type MenuProps } from "antd";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useMemo, useState, useSyncExternalStore, type ReactNode } from "react";
import { ForbiddenState } from "@/components/design-system/states";
import { TopBar } from "@/components/top-bar";
import { can, type Capability, type UserSession } from "@/lib/api";

const SIDEBAR_STORAGE_KEY = "contact-outreach:sidebar-collapsed";
const SIDEBAR_CHANGE_EVENT = "contact-outreach:sidebar-change";

type NavigationItem = {
  // The section's own route; also its menu key.
  key: string;
  label: string;
  icon: ReactNode;
  // The person needs at least one of these to see the section; absent for sections open to every role.
  capabilities?: readonly Capability[];
  // Other routes that belong to this section and keep it highlighted (its tabs, related pages).
  matches?: readonly string[];
  // Where the link goes when the first tab is not one this person may open.
  fallbackHref?: { capability: Capability; href: string };
};

type NavigationGroup = {
  id: string;
  label: string;
  items: readonly NavigationItem[];
  // A folded group remembers its state; it is always open while one of its pages is showing.
  foldable?: boolean;
};

const navigationGroups: readonly NavigationGroup[] = [
  {
    id: "daily",
    label: "Día a día",
    items: [
      { key: "/dashboard", label: "Resumen", icon: <DashboardOutlined />, matches: ["/attention"] },
      { key: "/campaigns", label: "Campañas", icon: <NotificationOutlined /> },
      { key: "/responses", label: "Correos", icon: <MessageOutlined />, matches: ["/outbound"] },
      { key: "/contacts", label: "Contactos", icon: <TeamOutlined /> },
      {
        key: "/prospects",
        label: "Audiencia",
        icon: <ContactsOutlined />,
        capabilities: ["manage_campaigns", "manage_configuration"],
        matches: ["/settings/relevance"],
        fallbackHref: { capability: "manage_campaigns", href: "/settings/relevance" },
      },
      {
        key: "/automation",
        label: "Respuestas automáticas",
        icon: <RobotOutlined />,
        capabilities: ["manage_automation"],
        matches: ["/settings/prompts"],
      },
    ],
  },
  {
    id: "company",
    label: "Mi empresa",
    items: [
      { key: "/settings/profile", label: "Perfil comercial", icon: <ShopOutlined />, capabilities: ["manage_configuration"] },
      { key: "/settings/message-templates", label: "Mensajes de campaña", icon: <MailOutlined />, capabilities: ["manage_configuration"] },
      { key: "/catalogs", label: "Catálogos", icon: <BookOutlined />, capabilities: ["manage_configuration"] },
      { key: "/settings/categories", label: "Rubros", icon: <TagsOutlined />, capabilities: ["manage_configuration"] },
    ],
  },
  {
    id: "advanced",
    label: "Avanzado",
    foldable: true,
    items: [
      { key: "/settings/integrations", label: "Integraciones", icon: <ApiOutlined />, capabilities: ["manage_integrations"] },
      { key: "/settings/suppressions", label: "Correos bloqueados", icon: <StopOutlined />, capabilities: ["manage_contacts"] },
      { key: "/jobs", label: "Actividad del sistema", icon: <ClockCircleOutlined />, capabilities: ["view_jobs"] },
      { key: "/settings/overture", label: "Zonas con datos", icon: <EnvironmentOutlined />, capabilities: ["manage_integrations"] },
      { key: "/settings/users", label: "Usuarios", icon: <UserOutlined />, capabilities: ["manage_users"] },
      { key: "/audit", label: "Auditoría", icon: <AuditOutlined />, capabilities: ["view_audit"] },
    ],
  },
];

const allItems: readonly NavigationItem[] = navigationGroups.flatMap((group) => group.items);

// Every route that needs a capability. The shell only sees the path, so each tab of a section is a
// real route with its own entry here, and the create pages (no menu entry) are listed as well.
const restrictedRoutes: readonly { route: string; capability: Capability }[] = [
  { route: "/prospects", capability: "manage_campaigns" },
  { route: "/settings/relevance", capability: "manage_configuration" },
  { route: "/settings/profile", capability: "manage_configuration" },
  { route: "/settings/message-templates", capability: "manage_configuration" },
  { route: "/catalogs", capability: "manage_configuration" },
  { route: "/settings/categories", capability: "manage_configuration" },
  { route: "/automation", capability: "manage_automation" },
  { route: "/settings/prompts", capability: "manage_automation" },
  { route: "/settings/integrations", capability: "manage_integrations" },
  { route: "/settings/overture", capability: "manage_integrations" },
  { route: "/settings/suppressions", capability: "manage_contacts" },
  { route: "/settings/users", capability: "manage_users" },
  { route: "/audit", capability: "view_audit" },
  { route: "/jobs", capability: "view_jobs" },
  { route: "/campaigns/new", capability: "manage_campaigns" },
  { route: "/contacts/new", capability: "manage_contacts" },
];

type SessionLike = { capabilities?: readonly string[] } | null | undefined;

function useMobileShell(): boolean {
  return useSyncExternalStore(
    (onStoreChange) => {
      const query = window.matchMedia("(max-width: 1023px)");
      query.addEventListener("change", onStoreChange);
      return () => query.removeEventListener("change", onStoreChange);
    },
    () => window.matchMedia("(max-width: 1023px)").matches,
    () => false,
  );
}

function subscribeToSidebarState(onStoreChange: () => void): () => void {
  const onStorage = (event: StorageEvent) => {
    if (event.key === SIDEBAR_STORAGE_KEY) onStoreChange();
  };
  window.addEventListener("storage", onStorage);
  window.addEventListener(SIDEBAR_CHANGE_EVENT, onStoreChange);
  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener(SIDEBAR_CHANGE_EVENT, onStoreChange);
  };
}

function getSidebarState(): boolean {
  return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true";
}

function useCollapsedSidebar(): boolean {
  return useSyncExternalStore(subscribeToSidebarState, getSidebarState, () => false);
}

const ADVANCED_STORAGE_KEY = "contact-outreach:nav-advanced-folded";
const ADVANCED_CHANGE_EVENT = "contact-outreach:nav-advanced-change";

function subscribeToAdvanced(onStoreChange: () => void): () => void {
  const onStorage = (event: StorageEvent) => {
    if (event.key === ADVANCED_STORAGE_KEY) onStoreChange();
  };
  window.addEventListener("storage", onStorage);
  window.addEventListener(ADVANCED_CHANGE_EVENT, onStoreChange);
  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener(ADVANCED_CHANGE_EVENT, onStoreChange);
  };
}

// Folded unless the person opened it before: the advanced pages are the ones used least.
function useFoldedAdvanced(): boolean {
  return useSyncExternalStore(
    subscribeToAdvanced,
    () => window.localStorage.getItem(ADVANCED_STORAGE_KEY) !== "false",
    () => true,
  );
}

function toggleFoldedAdvanced() {
  const folded = window.localStorage.getItem(ADVANCED_STORAGE_KEY) !== "false";
  window.localStorage.setItem(ADVANCED_STORAGE_KEY, String(!folded));
  window.dispatchEvent(new Event(ADVANCED_CHANGE_EVENT));
}

function activeNavigationKey(pathname: string): string {
  const candidates = allItems
    .flatMap((item) => [item.key, ...(item.matches ?? [])].map((route) => ({ route, key: item.key })))
    .sort((left, right) => right.route.length - left.route.length);
  return candidates.find(({ route }) => pathname === route || pathname.startsWith(`${route}/`))?.key ?? "";
}

function itemHref(item: NavigationItem, session: SessionLike): string {
  const { fallbackHref } = item;
  return fallbackHref && !can(session, fallbackHref.capability) ? fallbackHref.href : item.key;
}

function itemVisible(item: NavigationItem, session: SessionLike): boolean {
  return !item.capabilities || item.capabilities.some((capability) => can(session, capability));
}

function toMenuItems(
  items: readonly NavigationItem[],
  activeKey: string,
  session: SessionLike,
  onNavigate?: () => void,
): MenuProps["items"] {
  return items.map((item) => ({
    key: item.key,
    icon: item.icon,
    className: `nav-item${activeKey === item.key ? " nav-item--active" : ""}`,
    label: <Link href={itemHref(item, session)} onClick={onNavigate}>{item.label}</Link>,
  }));
}

type SidebarNavigationProps = {
  activeKey: string;
  collapsed: boolean;
  session: SessionLike;
  onNavigate?: () => void;
};

function SidebarGroup({
  group,
  activeKey,
  collapsed,
  session,
  onNavigate,
}: SidebarNavigationProps & { group: NavigationGroup }) {
  const foldedPreference = useFoldedAdvanced();
  const items = group.items.filter((item) => itemVisible(item, session));
  if (items.length === 0) return null;
  // Nothing to fold in the narrow rail (no room for a title) or while a page of the group is
  // showing: the group stays open so the highlighted item is never hidden.
  const holdsActive = items.some((item) => item.key === activeKey);
  const foldable = group.foldable === true && !collapsed && !holdsActive;
  const open = !foldable || !foldedPreference;
  const bodyId = `nav-group-${group.id}`;
  return (
    <div className={`nav-group${collapsed ? " nav-group--collapsed" : ""}`}>
      {foldable ? (
        <button
          type="button"
          className="nav-group__label nav-group__toggle"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={toggleFoldedAdvanced}
        >
          <span>{group.label}</span>
          <DownOutlined aria-hidden className="nav-group__chevron" />
        </button>
      ) : (
        <div className="nav-group__label"><span>{group.label}</span></div>
      )}
      <div id={bodyId} hidden={!open}>
        <Menu
          mode="inline"
          inlineCollapsed={collapsed}
          selectedKeys={[activeKey]}
          items={toMenuItems(items, activeKey, session, onNavigate)}
        />
      </div>
    </div>
  );
}

function SidebarNavigation(props: SidebarNavigationProps) {
  return (
    <nav className="sidebar-nav" aria-label="Navegación principal">
      {navigationGroups.map((group) => (
        <SidebarGroup key={group.id} group={group} {...props} />
      ))}
    </nav>
  );
}

export function visibleAdministrationItems(session: SessionLike): readonly NavigationItem[] {
  return allItems.filter((item) => item.capabilities && itemVisible(item, session));
}

export function canSeeAdministration(session: SessionLike): boolean {
  return visibleAdministrationItems(session).length > 0;
}

export function isForbiddenShellRoute(session: SessionLike, pathname: string): boolean {
  return restrictedRoutes.some(
    ({ route, capability }) => (pathname === route || pathname.startsWith(`${route}/`)) && !can(session, capability),
  );
}

export type AppShellProps = {
  session: UserSession;
  onSignOut: () => Promise<void>;
  children: ReactNode;
};

export function AppShell({ session, onSignOut, children }: AppShellProps) {
  const pathname = usePathname();
  const mobile = useMobileShell();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const collapsed = useCollapsedSidebar();
  const activeKey = useMemo(() => activeNavigationKey(pathname), [pathname]);

  const toggleCollapsed = () => {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(!collapsed));
    window.dispatchEvent(new Event(SIDEBAR_CHANGE_EVENT));
  };

  const navigation = (
    <div className="sidebar">
      <div className={`sidebar__brand${collapsed && !mobile ? " sidebar__brand--collapsed" : ""}`}>
        {collapsed && !mobile ? "CO" : "Contact Outreach"}
      </div>
      <SidebarNavigation
        activeKey={activeKey}
        collapsed={collapsed && !mobile}
        session={session}
        onNavigate={mobile ? () => setDrawerOpen(false) : undefined}
      />
      {!mobile ? (
        <div className="sidebar__collapse">
          <Tooltip title={collapsed ? "Expandir navegación" : "Contraer navegación"} placement="right">
            <Button
              id="sidebar-collapse-toggle"
              type="text"
              icon={collapsed ? <RightOutlined /> : <LeftOutlined />}
              aria-label={collapsed ? "Expandir navegación" : "Contraer navegación"}
              onClick={toggleCollapsed}
            />
          </Tooltip>
        </div>
      ) : null}
    </div>
  );

  const page = isForbiddenShellRoute(session, pathname)
    ? <ForbiddenState resource="esta sección de administración" />
    : children;

  return (
    <Layout className="app-shell">
      {mobile ? (
        <Drawer
          className="app-shell__drawer"
          placement="left"
          width={256}
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          closable={false}
          styles={{ body: { padding: 0 } }}
        >
          {navigation}
        </Drawer>
      ) : (
        <Layout.Sider
          className="app-shell__sider"
          width={256}
          collapsedWidth={64}
          collapsed={collapsed}
          trigger={null}
          theme="light"
          style={{ transition: "none" }}
        >
          {navigation}
        </Layout.Sider>
      )}
      <Layout className="app-shell__main">
        <TopBar
          session={session}
          mobile={mobile}
          onOpenNavigation={() => setDrawerOpen(true)}
          onSignOut={onSignOut}
        />
        <Layout.Content className="app-content">
          <div className="app-content__inner">{page}</div>
        </Layout.Content>
      </Layout>
    </Layout>
  );
}

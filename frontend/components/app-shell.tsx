"use client";

import {
  ApiOutlined,
  AuditOutlined,
  BookOutlined,
  ClockCircleOutlined,
  DashboardOutlined,
  EditOutlined,
  ExclamationCircleOutlined,
  LeftOutlined,
  MailOutlined,
  MessageOutlined,
  NotificationOutlined,
  RightOutlined,
  RobotOutlined,
  SearchOutlined,
  SendOutlined,
  ShopOutlined,
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
import type { UserSession } from "@/lib/api";

const SIDEBAR_STORAGE_KEY = "contact-outreach:sidebar-collapsed";
const SIDEBAR_CHANGE_EVENT = "contact-outreach:sidebar-change";

type NavigationItem = {
  key: string;
  label: string;
  icon: ReactNode;
};

const operationItems: readonly NavigationItem[] = [
  { key: "/dashboard", label: "Resumen", icon: <DashboardOutlined /> },
  { key: "/contacts", label: "Contactos", icon: <TeamOutlined /> },
  { key: "/campaigns", label: "Campañas", icon: <NotificationOutlined /> },
  { key: "/responses", label: "Respuestas", icon: <MessageOutlined /> },
  { key: "/attention", label: "Necesita atención", icon: <ExclamationCircleOutlined /> },
  { key: "/outbound", label: "Envíos", icon: <SendOutlined /> },
];

const administrationItems: readonly NavigationItem[] = [
  { key: "/catalogs", label: "Catálogos", icon: <BookOutlined /> },
  { key: "/settings/profile", label: "Perfil comercial", icon: <ShopOutlined /> },
  { key: "/settings/message-templates", label: "Mensajes de campaña", icon: <MailOutlined /> },
  { key: "/settings/prompts", label: "Instrucciones", icon: <EditOutlined /> },
  { key: "/settings/categories", label: "Rubros", icon: <TagsOutlined /> },
  { key: "/automation", label: "Automatización", icon: <RobotOutlined /> },
  { key: "/settings/integrations", label: "Integraciones", icon: <ApiOutlined /> },
  { key: "/settings/overture", label: "Datos de búsqueda", icon: <SearchOutlined /> },
  { key: "/settings/users", label: "Usuarios", icon: <UserOutlined /> },
  { key: "/audit", label: "Auditoría", icon: <AuditOutlined /> },
  { key: "/jobs", label: "Tareas", icon: <ClockCircleOutlined /> },
];

const administrationRoutePrefixes = administrationItems.map(({ key }) => key);
const adminOnlyCreateRoutes = ["/campaigns/new", "/contacts/new"];

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

function activeNavigationKey(pathname: string): string {
  const candidates = [...operationItems, ...administrationItems]
    .map(({ key }) => key)
    .sort((left, right) => right.length - left.length);
  return candidates.find((key) => pathname === key || pathname.startsWith(`${key}/`)) ?? "";
}

function toMenuItems(items: readonly NavigationItem[], activeKey: string, onNavigate?: () => void): MenuProps["items"] {
  return items.map(({ key, label, icon }) => ({
    key,
    icon,
    className: `nav-item${activeKey === key ? " nav-item--active" : ""}`,
    label: <Link href={key} onClick={onNavigate}>{label}</Link>,
  }));
}

type SidebarNavigationProps = {
  activeKey: string;
  collapsed: boolean;
  isAdmin: boolean;
  onNavigate?: () => void;
};

function SidebarNavigation({ activeKey, collapsed, isAdmin, onNavigate }: SidebarNavigationProps) {
  return (
    <nav className="sidebar-nav" aria-label="Navegación principal">
      <div className={`nav-group${collapsed ? " nav-group--collapsed" : ""}`}>
        <div className="nav-group__label"><span>Operación</span></div>
        <Menu
          mode="inline"
          inlineCollapsed={collapsed}
          selectedKeys={[activeKey]}
          items={toMenuItems(operationItems, activeKey, onNavigate)}
        />
      </div>
      {isAdmin ? (
        <div className={`nav-group${collapsed ? " nav-group--collapsed" : ""}`}>
          <div className="nav-group__label"><span>Administración</span></div>
          <Menu
            mode="inline"
            inlineCollapsed={collapsed}
            selectedKeys={[activeKey]}
            items={toMenuItems(administrationItems, activeKey, onNavigate)}
          />
        </div>
      ) : null}
    </nav>
  );
}

function sellerCannotReach(pathname: string): boolean {
  return [...administrationRoutePrefixes, ...adminOnlyCreateRoutes].some(
    (route) => pathname === route || pathname.startsWith(`${route}/`),
  );
}

export function canSeeAdministration(role: UserSession["role"]): boolean {
  return role === "ADMIN";
}

export function isForbiddenShellRoute(role: UserSession["role"], pathname: string): boolean {
  return role === "VENDEDOR" && sellerCannotReach(pathname);
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
  const isAdmin = canSeeAdministration(session.role);

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
        isAdmin={isAdmin}
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

  const page = isForbiddenShellRoute(session.role, pathname)
    ? <ForbiddenState resource="esta sección de administración" />
    : children;

  return (
    <Layout className="app-shell">
      {mobile ? (
        <Drawer
          className="app-shell__drawer"
          placement="left"
          width={240}
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
          width={240}
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

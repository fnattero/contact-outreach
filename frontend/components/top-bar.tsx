"use client";

import {
  DownOutlined,
  LogoutOutlined,
  MenuOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { Button, Dropdown, Skeleton, Tooltip, type MenuProps } from "antd";
import { useEffect, useState } from "react";
import { StatusBadge, displayValueMap } from "@/components/design-system/status-badge";
import { getDashboardSummary, type UserSession } from "@/lib/api";

type SendingModeState =
  | { status: "loading" }
  | { status: "ready"; value: "dry-run" | "live" }
  | { status: "error" };

export type TopBarProps = {
  session: UserSession;
  mobile: boolean;
  onOpenNavigation: () => void;
  onSignOut: () => Promise<void>;
};

function isSendingMode(value: string): value is "dry-run" | "live" {
  return value === "dry-run" || value === "live";
}

export function TopBar({ session, mobile, onOpenNavigation, onSignOut }: TopBarProps) {
  const [sendingMode, setSendingMode] = useState<SendingModeState>({ status: "loading" });

  useEffect(() => {
    let active = true;
    const loadSendingMode = async () => {
      try {
        const summary = await getDashboardSummary();
        if (!active) return;
        setSendingMode(isSendingMode(summary.safety.send_mode)
          ? { status: "ready", value: summary.safety.send_mode }
          : { status: "error" });
      } catch {
        if (active) setSendingMode({ status: "error" });
      }
    };
    const refreshOnFocus = () => void loadSendingMode();
    void loadSendingMode();
    window.addEventListener("focus", refreshOnFocus);
    return () => {
      active = false;
      window.removeEventListener("focus", refreshOnFocus);
    };
  }, []);

  const userItems: MenuProps["items"] = [
    {
      type: "group",
      label: (
        <span className="user-menu__identity">
          <span>{session.username}</span>
          <span>{session.role === "ADMIN" ? "Administrador/a" : "Vendedor/a"}</span>
        </span>
      ),
      children: [
        { key: "logout", icon: <LogoutOutlined />, label: "Cerrar sesión" },
      ],
    },
  ];

  const sendingModeIndicator = (() => {
    if (sendingMode.status === "loading") {
      return <Skeleton.Button active={false} size="small" className="topbar__mode-skeleton" />;
    }

    if (sendingMode.status === "error") {
      return (
        <Tooltip title="No se pudo consultar el modo de envío. Recargá la página para volver a intentarlo.">
          <span className="topbar__mode-trigger" tabIndex={0}><StatusBadge label="Modo no disponible" level="warning" /></span>
        </Tooltip>
      );
    }

    const definition = displayValueMap[sendingMode.value];
    return (
      <Tooltip title={definition.explanation}>
        <span className="topbar__mode-trigger" tabIndex={0}><StatusBadge value={sendingMode.value} /></span>
      </Tooltip>
    );
  })();

  return (
    <header className="topbar">
      <div className="topbar__workspace">
        {mobile ? (
          <Tooltip title="Abrir navegación">
            <Button
              id="mobile-navigation-toggle"
              className="topbar__menu-button"
              type="text"
              icon={<MenuOutlined />}
              aria-label="Abrir navegación"
              onClick={onOpenNavigation}
            />
          </Tooltip>
        ) : null}
        <span className="topbar__workspace-name">{session.workspace_name}</span>
      </div>
      <div className="topbar__actions">
        <div className="topbar__mode" aria-label="Modo de envío actual">
          {sendingModeIndicator}
        </div>
        <Dropdown
          menu={{
            items: userItems,
            onClick: ({ key }) => {
              if (key === "logout") void onSignOut();
            },
          }}
          placement="bottomRight"
          trigger={["click"]}
        >
          <Tooltip title="Abrir menú de usuario">
            <Button id="user-menu-trigger" className="user-menu__trigger" type="text" aria-label="Abrir menú de usuario">
              <UserOutlined aria-hidden />
              <span className="user-menu__name">{session.username}</span>
              <DownOutlined className="user-menu__chevron" aria-hidden />
            </Button>
          </Tooltip>
        </Dropdown>
      </div>
    </header>
  );
}

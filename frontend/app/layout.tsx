import type { Metadata } from "next";
import type { ReactNode } from "react";
import { AppProviders } from "@/components/app-providers";
import "antd/dist/reset.css";
import "./styles.css";

export const metadata: Metadata = {
  title: "Contact Outreach",
  description: "Panel privado de gestión de contactos y campañas",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="es">
      <body>
        <AppProviders>{children}</AppProviders>
      </body>
    </html>
  );
}

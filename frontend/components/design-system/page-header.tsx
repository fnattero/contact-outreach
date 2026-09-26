import type { ReactNode } from "react";

export type PageHeaderProps = {
  title: string;
  description: string;
  breadcrumbs?: ReactNode;
  status?: ReactNode;
  filters?: ReactNode;
  primaryAction?: ReactNode;
};

export function PageHeader({
  title,
  description,
  breadcrumbs,
  status,
  filters,
  primaryAction,
}: PageHeaderProps) {
  return (
    <header className="page-header">
      <div className="page-header__inner">
        {breadcrumbs ? <nav className="page-header__breadcrumbs" aria-label="Migas de pan">{breadcrumbs}</nav> : null}
        <div className="page-header__heading-row">
          <div className="page-header__copy">
            <h1 className="type-display">{title}</h1>
            <p className="page-header__description">{description}</p>
          </div>
          {status || primaryAction ? (
            <div className="page-header__actions">
              {status}
              {primaryAction}
            </div>
          ) : null}
        </div>
        {filters ? <div className="page-header__filters">{filters}</div> : null}
      </div>
    </header>
  );
}

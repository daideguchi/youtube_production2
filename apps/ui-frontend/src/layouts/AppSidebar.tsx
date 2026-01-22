import { memo } from "react";
import { NavLink, matchPath } from "react-router-dom";

export type NavItem = { key: string; label: string; icon: string; path: string };
export type NavSection = { title: string; items: NavItem[] };

interface AppSidebarProps {
  navSections: NavSection[];
  pathname: string;
  repoLabel?: string | null;
  buildLabel?: string | null;
  className?: string;
  showCloseButton?: boolean;
  onClose?: () => void;
  onNavigate?: () => void;
}

export const AppSidebar = memo(function AppSidebar({
  navSections,
  pathname,
  repoLabel,
  buildLabel,
  className,
  showCloseButton,
  onClose,
  onNavigate,
}: AppSidebarProps) {
  const isChannelWorkspaceRoute =
    Boolean(matchPath("/channels/:channelCode/videos/:video", pathname) || matchPath("/channels/:channelCode", pathname)) ||
    pathname.startsWith("/channel-workspace");
  const isChannelPortalRoute = Boolean(matchPath("/channels/:channelCode/portal", pathname));
  const isAudioIntegrityRoute = pathname.startsWith("/audio-integrity");

  return (
    <aside className={className ?? "shell-sidebar"}>
      <div className="shell-sidebar__header">
        {showCloseButton && onClose ? (
          <button type="button" className="shell-sidebar__close" onClick={onClose} aria-label="メニューを閉じる">
            ×
          </button>
        ) : null}
        <div className="shell-sidebar__brand">
          <span className="shell-avatar" aria-hidden>
            QC
          </span>
          <div>
            <h2 className="shell-sidebar__title">AI 制作スタジオ</h2>
            <p className="shell-sidebar__subtitle">品質管理コンソール</p>
            {repoLabel || buildLabel ? (
              <p className="shell-sidebar__build mono">
                {repoLabel ? <span className="shell-sidebar__build-repo">{repoLabel}</span> : null}
                {repoLabel && buildLabel ? <span className="shell-sidebar__build-sep"> · </span> : null}
                {buildLabel ? <span className="shell-sidebar__build-sha">{buildLabel}</span> : null}
              </p>
            ) : null}
          </div>
        </div>
      </div>

      <div className="shell-sidebar__scroll">
        <nav className="shell-nav" aria-label="主要メニュー">
          {navSections.map((section) => (
            <div key={section.title} className="shell-nav__section">
              <div className="shell-nav__section-title">{section.title}</div>
              {section.items.map((item) => {
                const isChannelWorkspaceItem = item.key === "channelWorkspace";
                const isChannelPortalItem = item.key === "channelPortal";
                const isAudioIntegrityItem = item.key === "audioIntegrity";
                return (
                  <NavLink
                    key={item.key}
                    to={item.path}
                    onClick={() => {
                      onNavigate?.();
                    }}
                    className={({ isActive }) => {
                      const active =
                        isActive ||
                        (isChannelWorkspaceItem && isChannelWorkspaceRoute) ||
                        (isChannelPortalItem && isChannelPortalRoute) ||
                        (isAudioIntegrityItem && isAudioIntegrityRoute);
                      return active ? "shell-nav__item shell-nav__item--active" : "shell-nav__item";
                    }}
                  >
                    <span className="shell-nav__icon" aria-hidden>
                      {item.icon}
                    </span>
                    <span>{item.label}</span>
                  </NavLink>
                );
              })}
            </div>
          ))}
        </nav>
      </div>
    </aside>
  );
});

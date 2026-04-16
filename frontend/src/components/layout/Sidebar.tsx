import { NavLink } from "react-router-dom";
import { ChevronLeft, ChevronRight, Settings, CreditCard, HelpCircle } from "lucide-react";
import { useSettingsStore } from "@/stores/settingsStore";
import { cn } from "@/components/ui/cn";
import { Tooltip } from "@/components/ui/Tooltip";
import { MODULES } from "./modules";

export function Sidebar() {
  const collapsed = useSettingsStore((s) => s.sidebarCollapsed);
  const toggle = useSettingsStore((s) => s.toggleSidebar);
  const width = collapsed ? "w-16" : "w-60";

  return (
    <aside
      className={cn(
        "flex shrink-0 flex-col border-r border-borderSubtle bg-bgSecondary transition-[width] duration-150",
        width,
      )}
    >
      <nav className="flex flex-1 flex-col gap-1 p-2">
        {MODULES.map((m) => {
          const Icon = m.icon;
          const link = (
            <NavLink
              key={m.key}
              to={m.path}
              className={({ isActive }) =>
                cn(
                  "group flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                  "hover:bg-bgHover",
                  isActive ? "bg-bgHover text-textPrimary" : "text-textSecondary",
                  collapsed && "justify-center px-0",
                )
              }
              style={({ isActive }) =>
                isActive ? ({ borderLeft: `3px solid ${m.color}`, paddingLeft: collapsed ? undefined : 9 } as const) : undefined
              }
            >
              <Icon size={20} strokeWidth={1.5} style={{ color: m.color }} />
              {!collapsed && <span className="truncate">{m.label}</span>}
            </NavLink>
          );
          return collapsed ? (
            <Tooltip key={m.key} label={m.label} side="right">
              {link}
            </Tooltip>
          ) : (
            link
          );
        })}
      </nav>
      <div className="mt-auto flex flex-col gap-1 border-t border-borderSubtle p-2">
        {[
          { to: "/settings", icon: Settings, label: "Settings" },
          { to: "/billing", icon: CreditCard, label: "Billing" },
          { to: "/help", icon: HelpCircle, label: "Help" },
        ].map((it) => {
          const Icon = it.icon;
          const link = (
            <NavLink
              to={it.to}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm text-textSecondary hover:bg-bgHover hover:text-textPrimary",
                  isActive && "bg-bgHover text-textPrimary",
                  collapsed && "justify-center px-0",
                )
              }
            >
              <Icon size={20} strokeWidth={1.5} />
              {!collapsed && <span>{it.label}</span>}
            </NavLink>
          );
          return collapsed ? (
            <Tooltip key={it.to} label={it.label} side="right">
              {link}
            </Tooltip>
          ) : (
            <div key={it.to}>{link}</div>
          );
        })}
        <button
          onClick={toggle}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className="mt-1 flex items-center justify-center rounded-md py-2 text-textMuted hover:bg-bgHover hover:text-textPrimary"
        >
          {collapsed ? <ChevronRight size={18} /> : <ChevronLeft size={18} />}
        </button>
      </div>
    </aside>
  );
}

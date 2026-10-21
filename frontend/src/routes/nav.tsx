/**
 * Navigation, filtered by permission.
 *
 * A destination whose endpoints would reject the user is not shown. That is a
 * usability decision -- the backend rejects the request regardless -- but it
 * matters: offering someone a Users page that 403s on arrival is worse than not
 * offering it.
 *
 * Everything an administrator alone sees sits in one Administration group,
 * every item badged: saying so stops a screen reading as a page that happens
 * to be broken for everyone else, and it used to be badged on two of five.
 */

import type { ReactNode } from "react";

import { PERMISSIONS, type Permission } from "@/api/types";

export interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  /** Hidden unless the user holds this. */
  permission?: Permission;
  group: string;
}

const icon = (path: ReactNode) => (
  <svg
    width="16"
    height="16"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
    style={{ flex: "none", opacity: 0.85 }}
  >
    {path}
  </svg>
);

export const NAV_ITEMS: NavItem[] = [
  {
    to: "/",
    label: "Overview",
    group: "Workspace",
    icon: icon(<><rect x="3" y="3" width="7" height="9" rx="1" /><rect x="14" y="3" width="7" height="5" rx="1" /><rect x="14" y="12" width="7" height="9" rx="1" /><rect x="3" y="16" width="7" height="5" rx="1" /></>),
  },
  {
    to: "/chat",
    label: "Chat",
    group: "Workspace",
    permission: PERMISSIONS.chatUse,
    icon: icon(<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />),
  },
  {
    to: "/files",
    label: "Files",
    group: "Workspace",
    icon: icon(<><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" /><path d="M13 2v7h7" /></>),
  },
  {
    to: "/skills",
    label: "Skills",
    group: "Workspace",
    // Listing is open to anyone who can hold a conversation: knowing what the
    // agent is able to do is part of using it. Import and delete are gated
    // server-side on plugins.manage, and those controls hide without it.
    permission: PERMISSIONS.chatUse,
    icon: icon(<><path d="M4 6h10M4 12h10M4 18h6" /><path d="M16 15l2.5 2.5L23 13" /></>),
  },
  {
    to: "/schedules",
    label: "My schedules",
    group: "Workspace",
    // Your own reminders and schedules, not the instance-wide list -- every
    // scheduler_my_* endpoint filters or checks by owner_id server-side, so
    // this is as open as Skills rather than gated on scheduler.manage.
    permission: PERMISSIONS.chatUse,
    icon: icon(<><rect x="3" y="4" width="18" height="18" rx="2" /><path d="M16 2v4M8 2v4M3 10h18" /><path d="M8 14h2M13 14h3M8 17h2M13 17h3" /></>),
  },
  {
    to: "/network",
    label: "Network",
    group: "Workspace",
    permission: PERMISSIONS.networkView,
    icon: icon(<><rect x="2" y="14" width="6" height="7" rx="1" /><rect x="16" y="14" width="6" height="7" rx="1" /><rect x="9" y="2" width="6" height="7" rx="1" /><path d="M12 9v3M5 14v-2h14v2" /></>),
  },
  {
    to: "/approvals",
    label: "Approvals",
    group: "Workspace",
    permission: PERMISSIONS.networkApprove,
    icon: icon(<><path d="M9 11l3 3L22 4" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" /></>),
  },
  {
    to: "/inventory",
    label: "Inventory",
    group: "Workspace",
    permission: PERMISSIONS.inventoryView,
    icon: icon(<><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>),
  },
  {
    to: "/drift",
    label: "Drift",
    group: "Workspace",
    permission: PERMISSIONS.inventoryView,
    icon: icon(<><path d="M3 12h4l3-8 4 16 3-8h4" /></>),
  },
  {
    to: "/users",
    label: "Users",
    group: "Administration",
    permission: PERMISSIONS.usersManage,
    icon: icon(<><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /></>),
  },
  {
    to: "/models",
    label: "Models",
    group: "Administration",
    permission: PERMISSIONS.settingsManage,
    icon: icon(<><path d="M4 6h16M4 12h16M4 18h10" /><circle cx="17" cy="18" r="2" /></>),
  },
  {
    to: "/plugins",
    label: "Plugins",
    group: "Administration",
    permission: PERMISSIONS.pluginsManage,
    icon: icon(<path d="M20 7h-9M14 17H5M17 3v8M7 13v8" />),
  },
  {
    to: "/settings",
    label: "Settings",
    group: "Workspace",
    icon: icon(<><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4" /></>),
  },
  {
    to: "/system",
    label: "System",
    group: "Administration",
    permission: PERMISSIONS.settingsManage,
    icon: icon(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>),
  },
  {
    to: "/activity",
    label: "Activity",
    group: "Administration",
    permission: PERMISSIONS.auditViewAll,
    icon: icon(<path d="M22 12h-4l-3 9L9 3l-3 9H2" />),
  },
];

export const NAV_GROUPS = ["Workspace", "Administration"];

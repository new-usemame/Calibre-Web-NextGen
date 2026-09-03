import type { LucideIcon } from 'lucide-react';
import {
  BarChart3,
  Clock,
  Database,
  FileText,
  Files,
  Mail,
  Server,
  Settings,
  Shield,
  SlidersHorizontal,
  Smartphone,
  Table2,
  Users,
} from 'lucide-react';

export interface ContextSidebarItem {
  key: string;
  href: string;
  label: string;
  icon: LucideIcon;
  /** SPA links use Wouter; classic links intentionally perform a full load. */
  spa?: boolean;
  /** A compact disclosure shown on links that leave the SPA for classic UI. */
  classic?: boolean;
  /** Marks the landing item active when no named section hash is selected. */
  defaultForPath?: boolean;
}

export interface ContextSidebarGroup {
  key: string;
  label: string;
  items: ContextSidebarItem[];
}

export interface ContextSidebarDefinition {
  key: string;
  label: string;
  matches: (location: string) => boolean;
  groups: ContextSidebarGroup[];
}

const ADMIN_CONTEXT: ContextSidebarDefinition = {
  key: 'admin',
  label: 'Admin navigation',
  matches: (location) => location === '/admin' || location.startsWith('/admin/'),
  groups: [
    {
      key: 'people-devices',
      label: 'People & devices',
      items: [
        {
          key: 'users',
          href: '/admin#user-administration',
          label: 'Users',
          icon: Users,
          spa: true,
          defaultForPath: true,
        },
        {
          key: 'devices',
          href: '/admin/devices',
          label: 'Devices',
          icon: Smartphone,
          spa: true,
        },
      ],
    },
    {
      key: 'configuration',
      label: 'Configuration',
      items: [
        {
          key: 'library-settings',
          href: '/admin#library-settings',
          label: 'Library',
          icon: Settings,
          spa: true,
        },
        {
          key: 'email-settings',
          href: '/admin#email-settings',
          label: 'Email server',
          icon: Mail,
          spa: true,
        },
        {
          key: 'security-settings',
          href: '/admin#security-settings',
          label: 'Security',
          icon: Shield,
          spa: true,
        },
      ],
    },
    {
      key: 'server-tools',
      label: 'Server tools',
      items: [
        { key: 'full-users', href: '/admin/view', label: 'User restrictions', icon: Table2, classic: true },
        { key: 'basic-config', href: '/admin/config', label: 'Basic settings', icon: Settings, classic: true },
        { key: 'display-config', href: '/admin/viewconfig', label: 'Display', icon: SlidersHorizontal, classic: true },
        { key: 'database-config', href: '/admin/dbconfig', label: 'Database path', icon: Database, classic: true },
        { key: 'scheduled-tasks', href: '/admin/scheduledtasks', label: 'Scheduled tasks', icon: Clock, classic: true },
        { key: 'cwa-settings', href: '/cwa-settings', label: 'CWA settings', icon: Server, classic: true },
        { key: 'duplicate-settings', href: '/cwa-settings#duplicate-detection', label: 'Duplicates', icon: Files, classic: true },
        { key: 'statistics', href: '/cwa-stats-show', label: 'Statistics', icon: BarChart3, classic: true },
        { key: 'logs', href: '/admin/logfile', label: 'Logs', icon: FileText, classic: true },
      ],
    },
  ],
};

const CONTEXT_SIDEBARS = [ADMIN_CONTEXT];

export function contextSidebarForLocation(location: string): ContextSidebarDefinition | null {
  return CONTEXT_SIDEBARS.find((context) => context.matches(location)) ?? null;
}

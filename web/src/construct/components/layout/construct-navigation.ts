import {
  Activity,
  Bot,
  Clock,
  LayoutDashboard,
  Monitor,
  ShieldCheck,
  Smartphone,
  Users,
  Workflow,
  Wrench,
  Puzzle,
  Palette,
  Database,
  Sparkles,
  Settings,
  Stethoscope,
  DollarSign,
  Network,
  type LucideIcon,
} from 'lucide-react';

export type V2NavItem = {
  to: string;
  label: string;
  labelKey: string;
  icon: LucideIcon;
  blurb: string;
  blurbKey: string;
};

export type V2NavSection = {
  id: string;
  label: string;
  labelKey: string;
  items: V2NavItem[];
};

export const v2NavSections: V2NavSection[] = [
  {
    id: 'orchestration',
    label: 'Orchestration',
    labelKey: 'nav.section.orchestration',
    items: [
      { to: '/dashboard', label: 'Dashboard', labelKey: 'nav.dashboard', icon: LayoutDashboard, blurb: 'Live runtime posture and active DAG state', blurbKey: 'nav.dashboard.blurb' },
      { to: '/workflows', label: 'Workflows', labelKey: 'nav.workflows', icon: Workflow, blurb: 'Define DAGs and drill into runs from the same flow', blurbKey: 'nav.workflows.blurb' },
      { to: '/agents', label: 'Agents', labelKey: 'nav.agents', icon: Bot, blurb: 'Reusable actors and workflow participants', blurbKey: 'nav.agents.blurb' },
      { to: '/canvas', label: 'Canvas', labelKey: 'nav.canvas', icon: Monitor, blurb: 'Visual workspace and orchestration canvases', blurbKey: 'nav.canvas.blurb' },
      { to: '/teams', label: 'Teams', labelKey: 'nav.teams', icon: Users, blurb: 'View delegation topology and ownership', blurbKey: 'nav.teams.blurb' },
    ],
  },
  {
    id: 'operations',
    label: 'Operations',
    labelKey: 'nav.section.operations',
    items: [
      { to: '/assets', label: 'Assets', labelKey: 'nav.assets', icon: Database, blurb: 'Artifacts and workflow-adjacent resources', blurbKey: 'nav.assets.blurb' },
      { to: '/skills', label: 'Skills', labelKey: 'nav.skills', icon: Sparkles, blurb: 'Reusable operational capabilities', blurbKey: 'nav.skills.blurb' },
      { to: '/tools', label: 'Tools', labelKey: 'nav.tools', icon: Wrench, blurb: 'Agent tool catalog and CLI binaries', blurbKey: 'nav.tools.blurb' },
      { to: '/integrations', label: 'Integrations', labelKey: 'nav.integrations', icon: Puzzle, blurb: 'Connected systems and channel surfaces', blurbKey: 'nav.integrations.blurb' },
      { to: '/cron', label: 'Cron', labelKey: 'nav.cron', icon: Clock, blurb: 'Scheduled jobs and catch-up behaviour', blurbKey: 'nav.cron.blurb' },
      { to: '/pairing', label: 'Pairing', labelKey: 'nav.pairing', icon: Smartphone, blurb: 'Device enrolment and trust management', blurbKey: 'nav.pairing.blurb' },
      { to: '/config', label: 'Config', labelKey: 'nav.config', icon: Settings, blurb: 'Runtime TOML and operational configuration', blurbKey: 'nav.config.blurb' },
      { to: '/cost', label: 'Cost', labelKey: 'nav.cost', icon: DollarSign, blurb: 'Spend and efficiency by workflow and run', blurbKey: 'nav.cost.blurb' },
      { to: '/skins', label: 'Skins', labelKey: 'nav.skins', icon: Palette, blurb: 'Token and asset based UI skins', blurbKey: 'nav.skins.blurb' },
    ],
  },
  {
    id: 'inspection',
    label: 'Inspection',
    labelKey: 'nav.section.inspection',
    items: [
      { to: '/memory', label: 'Memory', labelKey: 'nav.memory', icon: Network, blurb: 'Kumiho memory graph explorer and revisions', blurbKey: 'nav.memory.blurb' },
      { to: '/logs', label: 'Logs', labelKey: 'nav.logs', icon: Activity, blurb: 'Operational traces and node-linked events', blurbKey: 'nav.logs.blurb' },
      { to: '/audit', label: 'Audit', labelKey: 'nav.audit', icon: ShieldCheck, blurb: 'Trust chain and approval verification', blurbKey: 'nav.audit.blurb' },
      { to: '/doctor', label: 'Doctor', labelKey: 'nav.doctor', icon: Stethoscope, blurb: 'Diagnostics and runtime recovery posture', blurbKey: 'nav.doctor.blurb' },
    ],
  },
];

export const v2RouteMeta: Record<string, { title: string; description: string; titleKey: string; descriptionKey: string }> = Object.fromEntries(
  v2NavSections.flatMap((section) =>
    section.items.map((item) => [
      item.to,
      { title: item.label, description: item.blurb, titleKey: item.labelKey, descriptionKey: item.blurbKey },
    ] as const),
  ),
);

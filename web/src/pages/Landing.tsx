import { useRef, useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Bot,
  Brain,
  Network,
  Package,
  ShoppingBag,
  Target,
  Shield,
  ArrowRight,
  Zap,
  Globe,
  BookOpen,
  Database,
  Hash,
  Layers,
  GitBranch,
  Search,
  Radio,
  Terminal,
} from 'lucide-react';
import { appAssetPath } from '@/lib/basePath';
import { useT } from '@/revka/hooks/useT';

// ---------------------------------------------------------------------------
// Scroll reveal hook — uses IntersectionObserver + CSS transitions.
// Initialises visible=true when prefers-reduced-motion is active so no
// animation state is ever held back.
// ---------------------------------------------------------------------------
function useReveal(threshold = 0.1) {
  const ref = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(() =>
    typeof window !== 'undefined'
      ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
      : false,
  );

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        if (entry?.isIntersecting) {
          setVisible(true);
          observer.unobserve(el);
        }
      },
      { threshold },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [threshold]);

  return { ref, visible };
}

// ---------------------------------------------------------------------------
// Parallax hook — returns a translateY offset driven by scroll position.
// Respects prefers-reduced-motion by returning 0 always.
// ---------------------------------------------------------------------------
function useParallax(factor = 0.18) {
  const [offset, setOffset] = useState(0);
  const reducedMotion =
    typeof window !== 'undefined'
      ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
      : true;

  useEffect(() => {
    if (reducedMotion) return;
    const onScroll = () => setOffset(window.scrollY * factor);
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, [factor, reducedMotion]);

  return offset;
}

// ---------------------------------------------------------------------------
// Data — identifiers only; all user-visible text resolved via t() at render.
// ---------------------------------------------------------------------------
type IconComp = typeof Brain;

type FeatureDef = {
  id: string;
  icon: IconComp;
  titleKey: string;
  descKey: string;
  color: string;
  featured: boolean;
};

const FEATURES: FeatureDef[] = [
  {
    id: 'memory',
    icon: Brain,
    titleKey: 'landing.features.memory.title',
    descKey: 'landing.features.memory.desc',
    color: '#8fa8c8',
    featured: true,
  },
  {
    id: 'operator',
    icon: Network,
    titleKey: 'landing.features.operator.title',
    descKey: 'landing.features.operator.desc',
    color: '#6f83d6',
    featured: false,
  },
  {
    id: 'pool',
    icon: Package,
    titleKey: 'landing.features.pool.title',
    descKey: 'landing.features.pool.desc',
    color: '#6575d3',
    featured: false,
  },
  {
    id: 'clawhub',
    icon: ShoppingBag,
    titleKey: 'landing.features.clawhub.title',
    descKey: 'landing.features.clawhub.desc',
    color: '#f59e0b',
    featured: false,
  },
  {
    id: 'goals',
    icon: Target,
    titleKey: 'landing.features.goals.title',
    descKey: 'landing.features.goals.desc',
    color: '#2fbf75',
    featured: false,
  },
  {
    id: 'audit',
    icon: Shield,
    titleKey: 'landing.features.audit.title',
    descKey: 'landing.features.audit.desc',
    color: '#ff4466',
    featured: false,
  },
];

type NamespaceDef = {
  ns: string;
  purposeKey: string;
  icon: IconComp;
};

const NAMESPACES: NamespaceDef[] = [
  { ns: 'Revka/AgentPool/', purposeKey: 'landing.arch.ns.agentpool', icon: Package },
  { ns: 'Revka/Plans/', purposeKey: 'landing.arch.ns.plans', icon: Target },
  { ns: 'Revka/Sessions/', purposeKey: 'landing.arch.ns.sessions', icon: BookOpen },
  { ns: 'Revka/Goals/', purposeKey: 'landing.arch.ns.goals', icon: Globe },
  { ns: 'Revka/ClawHub/', purposeKey: 'landing.arch.ns.clawhub', icon: ShoppingBag },
  { ns: 'Revka/Teams/', purposeKey: 'landing.arch.ns.teams', icon: Network },
  { ns: 'CognitiveMemory/Skills/', purposeKey: 'landing.arch.ns.skills', icon: Database },
  { ns: 'Revka/AgentTrust/', purposeKey: 'landing.arch.ns.trust', icon: Shield },
];

type StatDef = { value: string; labelKey: string };

const STATS: StatDef[] = [
  { value: 'Rust', labelKey: 'landing.stats.runtime_label' },
  { value: '8', labelKey: 'landing.stats.namespaces_label' },
  { value: 'Neo4j', labelKey: 'landing.stats.memory_label' },
  { value: '3-tier', labelKey: 'landing.stats.goals_label' },
  { value: 'MCP', labelKey: 'landing.stats.orchestration_label' },
];

type PillarDef = {
  id: string;
  icon: IconComp;
  titleKey: string;
  descKey: string;
  color: string;
};

const PILLARS: PillarDef[] = [
  {
    id: 'agents',
    icon: Bot,
    titleKey: 'landing.pillars.agents.title',
    descKey: 'landing.pillars.agents.desc',
    color: '#6f83d6',
  },
  {
    id: 'memory',
    icon: Brain,
    titleKey: 'landing.pillars.memory.title',
    descKey: 'landing.pillars.memory.desc',
    color: '#8fa8c8',
  },
  {
    id: 'channels',
    icon: Radio,
    titleKey: 'landing.pillars.channels.title',
    descKey: 'landing.pillars.channels.desc',
    color: '#6575d3',
  },
];

type CliDef = { cmd: string; descKey: string };

const CLI_COMMANDS: CliDef[] = [
  { cmd: 'revka onboard', descKey: 'landing.cli.cmd.onboard' },
  { cmd: 'revka onboard --reinit', descKey: 'landing.cli.cmd.reinit' },
  { cmd: 'revka daemon', descKey: 'landing.cli.cmd.daemon' },
  { cmd: 'revka doctor', descKey: 'landing.cli.cmd.doctor' },
  { cmd: 'revka status', descKey: 'landing.cli.cmd.status' },
  { cmd: 'revka channel list', descKey: 'landing.cli.cmd.channel_list' },
  { cmd: 'revka channel add telegram \'{"bot_token":"..."}\'', descKey: 'landing.cli.cmd.channel_add' },
  { cmd: 'revka providers', descKey: 'landing.cli.cmd.providers' },
  { cmd: 'revka memory stats', descKey: 'landing.cli.cmd.memory_stats' },
  { cmd: 'revka service install', descKey: 'landing.cli.cmd.service_install' },
];

type TunnelDef = { id: string; nameKey: string; descKey: string };

const TUNNEL_PROVIDERS: TunnelDef[] = [
  {
    id: 'cloudflare',
    nameKey: 'landing.tunnels.provider.cloudflare.name',
    descKey: 'landing.tunnels.provider.cloudflare.desc',
  },
  {
    id: 'ngrok',
    nameKey: 'landing.tunnels.provider.ngrok.name',
    descKey: 'landing.tunnels.provider.ngrok.desc',
  },
  {
    id: 'tailscale',
    nameKey: 'landing.tunnels.provider.tailscale.name',
    descKey: 'landing.tunnels.provider.tailscale.desc',
  },
  {
    id: 'custom',
    nameKey: 'landing.tunnels.provider.custom.name',
    descKey: 'landing.tunnels.provider.custom.desc',
  },
];

type HowStepDef = {
  step: string;
  icon: IconComp;
  titleKey: string;
  descKey: string;
  color: string;
};

const HOW_IT_WORKS: HowStepDef[] = [
  {
    step: '01',
    icon: Layers,
    titleKey: 'landing.how.step1.title',
    descKey: 'landing.how.step1.desc',
    color: '#8fa8c8',
  },
  {
    step: '02',
    icon: GitBranch,
    titleKey: 'landing.how.step2.title',
    descKey: 'landing.how.step2.desc',
    color: '#6f83d6',
  },
  {
    step: '03',
    icon: Search,
    titleKey: 'landing.how.step3.title',
    descKey: 'landing.how.step3.desc',
    color: '#6575d3',
  },
];

// ---------------------------------------------------------------------------
// Feature card — bento-aware (first card spans 2 cols on lg)
// ---------------------------------------------------------------------------
function FeatureCard({
  icon: Icon,
  title,
  desc,
  color,
  delay,
  featured,
  featuredBadge,
}: {
  icon: IconComp;
  title: string;
  desc: string;
  color: string;
  delay: number;
  featured: boolean;
  featuredBadge: string;
}) {
  const { ref, visible } = useReveal(0.08);

  return (
    <div
      ref={ref as React.Ref<HTMLDivElement>}
      className={['card feature-card p-6 flex flex-col', featured ? 'feature-card--featured' : ''].join(' ')}
      style={{
        opacity: visible ? 1 : 0,
        transform: visible ? 'translateY(0)' : 'translateY(28px)',
        transition: `opacity 0.55s ease ${delay}ms, transform 0.55s ease ${delay}ms`,
      }}
    >
      <div
        className="inline-flex h-11 w-11 items-center justify-center rounded-2xl mb-4 flex-shrink-0"
        style={{
          background: `${color}1a`,
          border: `1px solid ${color}33`,
        }}
      >
        <Icon className="h-5 w-5" style={{ color }} />
      </div>
      <h3
        className="font-semibold text-base mb-2"
        style={{ color: 'var(--pc-text-primary)' }}
      >
        {title}
      </h3>
      <p
        className="text-sm leading-relaxed flex-1"
        style={{ color: 'var(--pc-text-muted)' }}
      >
        {desc}
      </p>
      {featured && (
        <div
          className="mt-5 pt-4 flex items-center gap-2 text-xs font-medium"
          style={{
            borderTop: `1px solid ${color}22`,
            color,
          }}
        >
          <Zap className="h-3 w-3" />
          {featuredBadge}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Namespace card (architecture section)
// ---------------------------------------------------------------------------
function NsCard({
  ns,
  purpose,
  icon: Icon,
  delay,
}: {
  ns: string;
  purpose: string;
  icon: IconComp;
  delay: number;
}) {
  const { ref, visible } = useReveal(0.05);

  return (
    <div
      ref={ref as React.Ref<HTMLDivElement>}
      className="glass-card px-4 py-3.5 flex items-start gap-3"
      style={{
        opacity: visible ? 1 : 0,
        transform: visible ? 'translateX(0)' : 'translateX(-16px)',
        transition: `opacity 0.5s ease ${delay}ms, transform 0.5s ease ${delay}ms`,
      }}
    >
      <Icon
        className="h-4 w-4 mt-0.5 flex-shrink-0"
        style={{ color: 'var(--pc-accent)' }}
      />
      <div className="min-w-0">
        <code
          className="text-xs font-mono block mb-0.5 truncate"
          style={{ color: 'var(--pc-accent-light)' }}
        >
          {ns}
        </code>
        <span
          className="text-xs"
          style={{ color: 'var(--pc-text-muted)' }}
        >
          {purpose}
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// How it works step
// ---------------------------------------------------------------------------
function HowStep({
  step,
  icon: Icon,
  title,
  desc,
  color,
  delay,
  isLast,
}: {
  step: string;
  icon: IconComp;
  title: string;
  desc: string;
  color: string;
  delay: number;
  isLast: boolean;
}) {
  const { ref, visible } = useReveal(0.1);

  return (
    <div
      ref={ref as React.Ref<HTMLDivElement>}
      className="how-step relative flex flex-col items-center text-center"
      style={{
        opacity: visible ? 1 : 0,
        transform: visible ? 'translateY(0)' : 'translateY(24px)',
        transition: `opacity 0.6s ease ${delay}ms, transform 0.6s ease ${delay}ms`,
      }}
    >
      {/* Step number */}
      <div
        className="how-step-num mb-4 text-xs font-mono font-bold tracking-widest"
        style={{ color: `${color}99` }}
      >
        {step}
      </div>

      {/* Icon ring */}
      <div
        className="how-step-icon mb-5"
        style={{
          background: `${color}14`,
          border: `1px solid ${color}33`,
          boxShadow: `0 0 32px ${color}18`,
        }}
      >
        <Icon className="h-7 w-7" style={{ color }} />
      </div>

      <h3
        className="font-semibold text-lg mb-2"
        style={{ color: 'var(--pc-text-primary)' }}
      >
        {title}
      </h3>
      <p
        className="text-sm leading-relaxed max-w-xs"
        style={{ color: 'var(--pc-text-muted)' }}
      >
        {desc}
      </p>

      {/* Connector line to next step (hidden on last) */}
      {!isLast && (
        <div
          className="how-step-connector"
          style={{ background: `linear-gradient(90deg, ${color}44, transparent)` }}
          aria-hidden="true"
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Landing page
// ---------------------------------------------------------------------------
export default function Landing() {
  const navigate = useNavigate();
  const { t } = useT();
  const [scrolled, setScrolled] = useState(false);
  const featuresRef = useRef<HTMLElement>(null);
  const bannerParallax = useParallax(0.12);

  // Sticky nav — becomes glass on scroll
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 48);
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  const { ref: badgeRef, visible: badgeVisible } = useReveal(0.01);
  const { ref: headlineRef, visible: headlineVisible } = useReveal(0.01);
  const { ref: heroImgRef, visible: heroImgVisible } = useReveal(0.01);
  const { ref: statsRef, visible: statsVisible } = useReveal(0.1);
  const { ref: pillarsTitleRef, visible: pillarsTitleVisible } = useReveal(0.08);
  const { ref: featuresTitleRef, visible: featuresTitleVisible } = useReveal(0.08);
  const { ref: howTitleRef, visible: howTitleVisible } = useReveal(0.08);
  const { ref: cliTitleRef, visible: cliTitleVisible } = useReveal(0.08);
  const { ref: archTitleRef, visible: archTitleVisible } = useReveal(0.08);
  const { ref: tunnelTitleRef, visible: tunnelTitleVisible } = useReveal(0.08);
  const { ref: ctaRef, visible: ctaVisible } = useReveal(0.1);

  const scrollToFeatures = useCallback(() => {
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    featuresRef.current?.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth' });
  }, []);

  return (
    <div
      className="landing-page min-h-screen overflow-x-hidden"
      style={{ background: 'var(--pc-bg-base)', color: 'var(--pc-text-primary)' }}
    >
      {/* ── Skip link — visible on keyboard focus, hidden otherwise ── */}
      <a href="#main-content" className="skip-link">
        {t('landing.skip_link')}
      </a>

      {/* ── Sticky Navigation ── */}
      <nav
        className={[
          'landing-nav sticky top-0 z-50 flex items-center justify-between px-6 py-4',
          scrolled ? 'landing-nav--scrolled' : '',
        ].join(' ')}
        aria-label={t('landing.nav.aria_label')}
      >
        <div className="flex items-center gap-2.5">
          <img
            src={appAssetPath('favicon-192.png')}
            alt={t('landing.nav.logo_alt')}
            className="h-8 w-8 rounded-xl object-cover"
            onError={(e) => {
              e.currentTarget.style.display = 'none';
            }}
          />
          <span className="font-bold text-base tracking-tight">Revka</span>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={scrollToFeatures}
            className="hidden sm:block text-sm font-medium transition-colors"
            style={{ color: 'var(--pc-text-muted)' }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLElement).style.color = 'var(--pc-text-primary)';
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLElement).style.color = 'var(--pc-text-muted)';
            }}
          >
            {t('landing.nav.features')}
          </button>
          <button
            onClick={() => navigate('/dashboard')}
            className="btn-electric flex items-center gap-1.5 px-5 py-2 text-sm font-semibold"
          >
            {t('landing.nav.launch')}
            <ArrowRight className="h-3.5 w-3.5" />
          </button>
        </div>
      </nav>

      <main id="main-content">

      {/* ── Hero ── */}
      <section
        className="hero-section relative flex min-h-[96vh] flex-col items-center justify-center px-4 pt-12 pb-0 overflow-hidden"
        aria-label={t('landing.hero.aria_label')}
      >
        {/* Ambient orbs */}
        <div className="hero-orb hero-orb-1" aria-hidden="true" />
        <div className="hero-orb hero-orb-2" aria-hidden="true" />
        <div className="hero-orb hero-orb-3" aria-hidden="true" />

        {/* Badge */}
        <div
          ref={badgeRef as React.Ref<HTMLDivElement>}
          className="shell-chip mb-7"
          style={{
            opacity: badgeVisible ? 1 : 0,
            transform: badgeVisible ? 'translateY(0)' : 'translateY(-12px)',
            transition: 'opacity 0.7s ease, transform 0.7s ease',
          }}
        >
          <Zap className="h-3 w-3" />
          {t('landing.hero.badge')}
        </div>

        {/* Headline + tagline — rendered BEFORE banner (Apple style) */}
        <div
          ref={headlineRef as React.Ref<HTMLDivElement>}
          className="text-center mb-10"
          style={{
            opacity: headlineVisible ? 1 : 0,
            transform: headlineVisible ? 'translateY(0)' : 'translateY(20px)',
            transition: 'opacity 0.7s ease 0.1s, transform 0.7s ease 0.1s',
          }}
        >
          <h1 className="text-5xl font-bold mb-4 tracking-tight sm:text-6xl lg:text-7xl xl:text-8xl">
            <span className="text-gradient-fox text-gradient-fox--shimmer">Revka</span>
          </h1>
          <p
            className="text-xl mb-3 max-w-xl mx-auto font-medium sm:text-2xl"
            style={{ color: 'var(--pc-text-secondary)' }}
          >
            {t('landing.hero.tagline')}
          </p>
          <p
            className="text-base mb-10 max-w-lg mx-auto leading-relaxed"
            style={{ color: 'var(--pc-text-muted)' }}
          >
            {t('landing.hero.description')}
          </p>

          {/* CTAs */}
          <div className="flex flex-col gap-3 sm:flex-row sm:justify-center">
            <button
              onClick={() => navigate('/dashboard')}
              className="btn-electric flex items-center justify-center gap-2 px-8 py-3.5 text-base font-semibold rounded-2xl"
            >
              {t('landing.hero.cta_primary')}
              <ArrowRight className="h-4 w-4" />
            </button>
            <button
              onClick={scrollToFeatures}
              className="shell-pill-button flex items-center justify-center gap-2 px-8 py-3.5 text-base font-semibold rounded-2xl"
            >
              {t('landing.hero.cta_secondary')}
            </button>
          </div>
        </div>

        {/* Banner image — below headline, parallax scroll */}
        <div
          ref={heroImgRef as React.Ref<HTMLDivElement>}
          className="hero-banner-wrap relative w-full"
          style={{
            opacity: heroImgVisible ? 1 : 0,
            transform: heroImgVisible
              ? `translateY(${bannerParallax}px)`
              : `scale(0.94) translateY(${16 + bannerParallax}px)`,
            transition: heroImgVisible
              ? 'opacity 0.9s ease 0.25s'
              : 'opacity 0.9s ease 0.25s, transform 0.9s ease 0.25s',
          }}
        >
          <img
            src={appAssetPath('revka-banner.png')}
            alt={t('landing.hero.banner_alt')}
            className="hero-banner hero-banner--v2"
            onError={(e) => {
              e.currentTarget.style.display = 'none';
            }}
          />
          <div className="hero-banner-glow" aria-hidden="true" />
          {/* Bottom fade to blend into next section */}
          <div className="hero-banner-fade" aria-hidden="true" />
        </div>

        {/* Scroll cue */}
        <div
          className="absolute bottom-8 left-1/2 -translate-x-1/2 flex flex-col items-center gap-2"
          aria-hidden="true"
          style={{
            opacity: headlineVisible ? 0.4 : 0,
            transition: 'opacity 1s ease 1.5s',
          }}
        >
          <span className="text-xs tracking-widest uppercase" style={{ color: 'var(--pc-text-faint)' }}>
            {t('landing.hero.scroll_cue')}
          </span>
          <div className="scroll-cue-line" />
        </div>
      </section>

      {/* ── Stats Strip ── */}
      <section
        className="stats-strip relative px-4 py-12"
        aria-label={t('landing.stats.aria_label')}
      >
        <div
          ref={statsRef as React.Ref<HTMLDivElement>}
          className="mx-auto max-w-4xl"
          style={{
            opacity: statsVisible ? 1 : 0,
            transform: statsVisible ? 'translateY(0)' : 'translateY(16px)',
            transition: 'opacity 0.6s ease, transform 0.6s ease',
          }}
        >
          <dl className="stats-list">
            {STATS.map((stat, i) => (
              <div
                key={stat.labelKey}
                className="stat-item"
                style={{
                  opacity: statsVisible ? 1 : 0,
                  transform: statsVisible ? 'translateY(0)' : 'translateY(12px)',
                  transition: `opacity 0.5s ease ${i * 80}ms, transform 0.5s ease ${i * 80}ms`,
                }}
              >
                <dt className="stat-value">{stat.value}</dt>
                <dd className="stat-label">{t(stat.labelKey)}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      {/* ── Pillars ── */}
      <section
        className="pillars-section relative px-4 py-20"
        aria-label={t('landing.pillars.aria_label')}
      >
        <div className="mx-auto max-w-5xl">
          <div
            ref={pillarsTitleRef as React.Ref<HTMLDivElement>}
            className="mb-10 text-center"
            style={{
              opacity: pillarsTitleVisible ? 1 : 0,
              transform: pillarsTitleVisible ? 'translateY(0)' : 'translateY(20px)',
              transition: 'opacity 0.6s ease, transform 0.6s ease',
            }}
          >
            <div className="shell-chip inline-flex mb-4">
              <Layers className="h-3 w-3" />
              {t('landing.pillars.badge')}
            </div>
            <h2
              className="text-3xl font-bold mb-3 sm:text-4xl"
              style={{ color: 'var(--pc-text-primary)' }}
            >
              {t('landing.pillars.title')}
            </h2>
            <p
              className="text-base max-w-2xl mx-auto leading-relaxed"
              style={{ color: 'var(--pc-text-muted)' }}
            >
              {t('landing.pillars.description')}
            </p>
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            {PILLARS.map((p, i) => {
              const Icon = p.icon;
              return (
                <div
                  key={p.id}
                  className="card p-6 flex flex-col"
                  style={{
                    opacity: pillarsTitleVisible ? 1 : 0,
                    transform: pillarsTitleVisible ? 'translateY(0)' : 'translateY(18px)',
                    transition: `opacity 0.55s ease ${120 + i * 80}ms, transform 0.55s ease ${120 + i * 80}ms`,
                  }}
                >
                  <div
                    className="inline-flex h-10 w-10 items-center justify-center rounded-2xl mb-4"
                    style={{
                      background: `${p.color}1a`,
                      border: `1px solid ${p.color}33`,
                    }}
                  >
                    <Icon className="h-5 w-5" style={{ color: p.color }} />
                  </div>
                  <h3
                    className="font-semibold text-base mb-2"
                    style={{ color: 'var(--pc-text-primary)' }}
                  >
                    {t(p.titleKey)}
                  </h3>
                  <p
                    className="text-sm leading-relaxed"
                    style={{ color: 'var(--pc-text-muted)' }}
                  >
                    {t(p.descKey)}
                  </p>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* ── Features (Bento) ── */}
      <section
        ref={featuresRef as React.Ref<HTMLElement>}
        className="features-section relative px-4 py-24"
        aria-label={t('landing.features.aria_label')}
      >
        <div className="mx-auto max-w-6xl">
          {/* Section title */}
          <div
            ref={featuresTitleRef as React.Ref<HTMLDivElement>}
            className="mb-12 text-center"
            style={{
              opacity: featuresTitleVisible ? 1 : 0,
              transform: featuresTitleVisible ? 'translateY(0)' : 'translateY(20px)',
              transition: 'opacity 0.6s ease, transform 0.6s ease',
            }}
          >
            <div className="shell-chip inline-flex mb-4">
              <Hash className="h-3 w-3" />
              {t('landing.features.badge')}
            </div>
            <h2
              className="text-3xl font-bold mb-3 sm:text-4xl"
              style={{ color: 'var(--pc-text-primary)' }}
            >
              {t('landing.features.title')}
            </h2>
            <p
              className="text-base max-w-2xl mx-auto leading-relaxed"
              style={{ color: 'var(--pc-text-muted)' }}
            >
              {t('landing.features.description')}
            </p>
          </div>

          {/* Bento grid */}
          <div className="bento-grid">
            {FEATURES.map((f, i) => (
              <div
                key={f.id}
                className={f.featured ? 'bento-cell bento-cell--wide' : 'bento-cell'}
              >
                <FeatureCard
                  icon={f.icon}
                  title={t(f.titleKey)}
                  desc={t(f.descKey)}
                  color={f.color}
                  featured={f.featured}
                  delay={i * 75}
                  featuredBadge={t('landing.features.featured_badge')}
                />
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── How It Works ── */}
      <section
        className="how-section relative px-4 py-24"
        aria-label={t('landing.how.aria_label')}
      >
        <div className="how-bg-orb" aria-hidden="true" />
        <div className="mx-auto max-w-5xl">
          <div
            ref={howTitleRef as React.Ref<HTMLDivElement>}
            className="mb-16 text-center"
            style={{
              opacity: howTitleVisible ? 1 : 0,
              transform: howTitleVisible ? 'translateY(0)' : 'translateY(20px)',
              transition: 'opacity 0.6s ease, transform 0.6s ease',
            }}
          >
            <div className="shell-chip inline-flex mb-4">
              <GitBranch className="h-3 w-3" />
              {t('landing.how.badge')}
            </div>
            <h2
              className="text-3xl font-bold mb-3 sm:text-4xl"
              style={{ color: 'var(--pc-text-primary)' }}
            >
              {t('landing.how.title')}
            </h2>
            <p
              className="text-base max-w-2xl mx-auto leading-relaxed"
              style={{ color: 'var(--pc-text-muted)' }}
            >
              {t('landing.how.description')}
            </p>
          </div>

          <div className="how-steps-grid">
            {HOW_IT_WORKS.map((step, i) => (
              <HowStep
                key={step.step}
                step={step.step}
                icon={step.icon}
                title={t(step.titleKey)}
                desc={t(step.descKey)}
                color={step.color}
                delay={i * 120}
                isLast={i === HOW_IT_WORKS.length - 1}
              />
            ))}
          </div>
        </div>
      </section>

      {/* ── CLI Reference ── */}
      <section
        className="cli-section relative px-4 py-24"
        aria-label={t('landing.cli.aria_label')}
      >
        <div className="mx-auto max-w-5xl">
          <div
            ref={cliTitleRef as React.Ref<HTMLDivElement>}
            className="mb-12 text-center"
            style={{
              opacity: cliTitleVisible ? 1 : 0,
              transform: cliTitleVisible ? 'translateY(0)' : 'translateY(20px)',
              transition: 'opacity 0.6s ease, transform 0.6s ease',
            }}
          >
            <div className="shell-chip inline-flex mb-4">
              <Terminal className="h-3 w-3" />
              {t('landing.cli.badge')}
            </div>
            <h2
              className="text-3xl font-bold mb-3 sm:text-4xl"
              style={{ color: 'var(--pc-text-primary)' }}
            >
              {t('landing.cli.title')}
            </h2>
            <p
              className="text-base max-w-2xl mx-auto leading-relaxed"
              style={{ color: 'var(--pc-text-muted)' }}
            >
              {t('landing.cli.description')}
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            {CLI_COMMANDS.map(({ cmd, descKey }, i) => (
              <div
                key={cmd}
                className="glass-card px-4 py-3"
                style={{
                  opacity: cliTitleVisible ? 1 : 0,
                  transform: cliTitleVisible ? 'translateY(0)' : 'translateY(14px)',
                  transition: `opacity 0.5s ease ${100 + i * 50}ms, transform 0.5s ease ${100 + i * 50}ms`,
                }}
              >
                <code
                  className="text-xs font-mono block mb-1"
                  style={{ color: 'var(--pc-accent-light)' }}
                >
                  {cmd}
                </code>
                <span
                  className="text-xs"
                  style={{ color: 'var(--pc-text-muted)' }}
                >
                  {t(descKey)}
                </span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Architecture ── */}
      <section
        className="arch-section relative px-4 py-24"
        aria-label={t('landing.arch.aria_label')}
      >
        <div className="arch-bg-orb" aria-hidden="true" />

        <div className="mx-auto max-w-5xl">
          <div
            ref={archTitleRef as React.Ref<HTMLDivElement>}
            className="mb-12 text-center"
            style={{
              opacity: archTitleVisible ? 1 : 0,
              transform: archTitleVisible ? 'translateY(0)' : 'translateY(20px)',
              transition: 'opacity 0.6s ease, transform 0.6s ease',
            }}
          >
            <div className="shell-chip inline-flex mb-4">
              <Database className="h-3 w-3" />
              {t('landing.arch.badge')}
            </div>
            <h2
              className="text-3xl font-bold mb-3 sm:text-4xl"
              style={{ color: 'var(--pc-text-primary)' }}
            >
              {t('landing.arch.title')}
            </h2>
            <p
              className="text-base max-w-2xl mx-auto leading-relaxed"
              style={{ color: 'var(--pc-text-muted)' }}
            >
              {t('landing.arch.description')}
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {NAMESPACES.map((n, i) => (
              <NsCard
                key={n.ns}
                ns={n.ns}
                purpose={t(n.purposeKey)}
                icon={n.icon}
                delay={i * 60}
              />
            ))}
          </div>

          <div
            className="arch-connect-bar mt-8"
            aria-hidden="true"
          />
          <p
            className="text-xs text-center mt-4"
            style={{ color: 'var(--pc-text-faint)' }}
          >
            {t('landing.arch.connection_note')}
          </p>
        </div>
      </section>

      {/* ── Tunnels ── */}
      <section
        className="tunnels-section relative px-4 py-24"
        aria-label={t('landing.tunnels.aria_label')}
      >
        <div className="mx-auto max-w-4xl">
          <div
            ref={tunnelTitleRef as React.Ref<HTMLDivElement>}
            className="mb-10 text-center"
            style={{
              opacity: tunnelTitleVisible ? 1 : 0,
              transform: tunnelTitleVisible ? 'translateY(0)' : 'translateY(20px)',
              transition: 'opacity 0.6s ease, transform 0.6s ease',
            }}
          >
            <div className="shell-chip inline-flex mb-4">
              <Globe className="h-3 w-3" />
              {t('landing.tunnels.badge')}
            </div>
            <h2
              className="text-3xl font-bold mb-3 sm:text-4xl"
              style={{ color: 'var(--pc-text-primary)' }}
            >
              {t('landing.tunnels.title')}
            </h2>
            <p
              className="text-base max-w-2xl mx-auto leading-relaxed"
              style={{ color: 'var(--pc-text-muted)' }}
            >
              {t('landing.tunnels.description_prefix')}
              <span className="font-mono" style={{ color: 'var(--pc-accent-light)' }}>127.0.0.1</span>
              {t('landing.tunnels.description_suffix')}
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            {TUNNEL_PROVIDERS.map((p, i) => (
              <div
                key={p.id}
                className="glass-card px-4 py-4"
                style={{
                  opacity: tunnelTitleVisible ? 1 : 0,
                  transform: tunnelTitleVisible ? 'translateY(0)' : 'translateY(14px)',
                  transition: `opacity 0.5s ease ${100 + i * 70}ms, transform 0.5s ease ${100 + i * 70}ms`,
                }}
              >
                <div
                  className="text-sm font-semibold mb-1"
                  style={{ color: 'var(--pc-text-primary)' }}
                >
                  {t(p.nameKey)}
                </div>
                <p
                  className="text-xs leading-relaxed"
                  style={{ color: 'var(--pc-text-muted)' }}
                >
                  {t(p.descKey)}
                </p>
              </div>
            ))}
          </div>

          <p
            className="text-xs text-center mt-6"
            style={{ color: 'var(--pc-text-faint)' }}
          >
            {t('landing.tunnels.setup_hint_prefix')}
            <span className="font-mono" style={{ color: 'var(--pc-accent-light)' }}>revka onboard --reinit</span>
            {t('landing.tunnels.setup_hint_suffix')}
          </p>
        </div>
      </section>

      {/* ── Final CTA ── */}
      <section
        className="cta-section relative px-4 py-32 text-center overflow-hidden"
        aria-label={t('landing.cta.aria_label')}
      >
        <div className="cta-orb-1" aria-hidden="true" />
        <div className="cta-orb-2" aria-hidden="true" />

        <div
          ref={ctaRef as React.Ref<HTMLDivElement>}
          className="relative mx-auto max-w-2xl"
          style={{
            opacity: ctaVisible ? 1 : 0,
            transform: ctaVisible ? 'translateY(0) scale(1)' : 'translateY(24px) scale(0.97)',
            transition: 'opacity 0.7s ease, transform 0.7s ease',
          }}
        >
          <img
            src={appAssetPath('favicon-192.png')}
            alt=""
            className="h-20 w-20 rounded-2xl object-cover mx-auto mb-6 animate-float"
            aria-hidden="true"
            onError={(e) => {
              e.currentTarget.style.display = 'none';
            }}
          />
          <h2
            className="text-4xl font-bold mb-4 sm:text-5xl"
            style={{ color: 'var(--pc-text-primary)' }}
          >
            {t('landing.cta.headline_prefix')}
            <span className="text-gradient-fox">{t('landing.cta.headline_highlight')}</span>
            {t('landing.cta.headline_suffix')}
          </h2>
          <p
            className="text-base mb-10 max-w-xl mx-auto leading-relaxed"
            style={{ color: 'var(--pc-text-muted)' }}
          >
            {t('landing.cta.description')}
          </p>
          <button
            onClick={() => navigate('/dashboard')}
            className="btn-electric inline-flex items-center gap-2 px-10 py-4 text-lg font-semibold rounded-2xl"
          >
            {t('landing.cta.button')}
            <ArrowRight className="h-5 w-5" />
          </button>
        </div>
      </section>

      </main>

      {/* ── Footer ── */}
      <footer
        className="landing-footer px-6 py-10"
        style={{ borderTop: '1px solid var(--pc-border)' }}
      >
        <div className="mx-auto max-w-5xl flex flex-col items-center gap-4 sm:flex-row sm:justify-between">
          <div className="flex items-center gap-2.5">
            <img
              src={appAssetPath('favicon-192.png')}
              alt=""
              className="h-6 w-6 rounded-lg object-cover"
              aria-hidden="true"
              onError={(e) => { e.currentTarget.style.display = 'none'; }}
            />
            <span
              className="text-sm font-semibold"
              style={{ color: 'var(--pc-text-secondary)' }}
            >
              Revka
            </span>
          </div>

          <div className="flex items-center gap-2 flex-wrap justify-center">
            {['Rust', 'React', 'Kumiho', 'Neo4j', 'MCP'].map((tech) => (
              <span key={tech} className="footer-tech-chip">{tech}</span>
            ))}
          </div>

          <p
            className="text-xs"
            style={{ color: 'var(--pc-text-faint)' }}
          >
            {t('landing.footer.tagline')}
          </p>
        </div>
      </footer>
    </div>
  );
}

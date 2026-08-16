import React, { useState } from 'react';
import {
  Button, IconButton, Tag, Card, Field, Input, Toggle, Segmented, Stars, ProgressBar, Icon,
  MediaTile, MediaRow, Sidebar, Topbar, Screen, StatCard, ListRow, Banner, EmptyState, ThemeSwitch,
  useTheme
} from './index.js';
import { TITLES, ADMIN_NAV, GUEST_NAV } from './data/sample.js';

function Section({ id, title, note, children }) {
  return (
    <section id={id} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--pa-space-4)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 'var(--pa-space-3)' }}>
        <h2 style={{ margin: 0, fontSize: 22, fontWeight: 400, letterSpacing: '-0.02em' }}>{title}</h2>
        {note && <span style={{ fontSize: 12, color: 'var(--pa-text-muted)' }}>{note}</span>}
        <span style={{ flex: 1, height: 1, background: 'linear-gradient(to right, rgba(236,238,244,0.16), transparent)' }} />
      </div>
      {children}
    </section>
  );
}

export default function Showcase() {
  const [theme, setTheme] = useTheme('rose');
  const [blurred, setBlurred] = useState(true);
  const [tab, setTab] = useState('All');
  const [rating, setRating] = useState(4);
  const [flags, setFlags] = useState({ privateLibs: true, pooledSearch: true, shorts: false });
  const set = (k) => (v) => setFlags((s) => ({ ...s, [k]: v }));

  return (
    <main style={{ display: 'flex', flexDirection: 'column', gap: 'var(--pa-space-7)', padding: '48px 48px 96px', maxWidth: 1420 }}>
      <header style={{ display: 'flex', flexDirection: 'column', gap: 'var(--pa-space-3)' }}>
        <span style={{ fontSize: 11, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--pa-accent-300)' }}>Pornarr UI</span>
        <h1 style={{ margin: 0, fontSize: 38, fontWeight: 400, letterSpacing: '-0.03em' }}>Design system — React + Vite</h1>
        <p style={{ margin: 0, maxWidth: 720, fontSize: 13, color: 'var(--pa-text-dim)', textWrap: 'pretty' }}>
          Every component reads the token set in <code style={{ fontFamily: 'var(--pa-font-mono)', color: 'var(--pa-accent-300)' }}>styles/tokens.css</code>.
          Two themes, one perceptual scale: the accent is a line and a glow, the 300 step carries small text, and
          placeholder artwork is anchored to the theme's art hue.
        </p>
        <ThemeSwitch value={theme} onChange={setTheme} />
      </header>

      <Section id="buttons" title="Buttons & tags" note="primary is an outline, never a fill">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--pa-space-3)', alignItems: 'center' }}>
          <Button variant="primary" icon="play">Resume</Button>
          <Button variant="secondary" icon="magnifying-glass">Search again</Button>
          <Button variant="ghost" icon="arrow-bend-up-left">Reply</Button>
          <Button variant="tonal" icon="check">Approve</Button>
          <IconButton icon="bell-simple" label="Notifications" />
          <Tag tone="accent">Needs approval</Tag>
          <Tag tone="neutral">2160p</Tag>
          <Tag tone="outline" icon="user-circle-minus">Another library</Tag>
        </div>
      </Section>

      <Section id="controls" title="Controls">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0,1fr))', gap: 'var(--pa-space-4)' }}>
          <Card title="Form">
            <Field label="Server name"><Input defaultValue="Kai's Pornarr" /></Field>
            <Field label="Root folder" hint="checked before it is saved">
              <Input icon="folder-open" defaultValue="/media/vault-a" />
            </Field>
          </Card>
          <Card title="Choice">
            <Segmented options={['All', 'Unwatched', '4★ and up']} value={tab} onChange={setTab} />
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Stars value={rating} size={17} onRate={setRating} />
              <span style={{ fontSize: 12, color: 'var(--pa-text-muted)' }}>{rating} of 5</span>
            </div>
            <ProgressBar value={66} />
          </Card>
          <Card title="House rules">
            <Toggle label="Private libraries" note="each person browses only their own" checked={flags.privateLibs} onChange={set('privateLibs')} />
            <Toggle label="Pooled search" note="owner names never shown" checked={flags.pooledSearch} onChange={set('pooledSearch')} />
            <Toggle label="Shorts in the feed" note="clips under a minute" checked={flags.shorts} onChange={set('shorts')} />
          </Card>
        </div>
      </Section>

      <Section id="media" title="Media" note="artwork is a placeholder that follows the theme">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, minmax(0,1fr))', gap: 'var(--pa-space-4)' }}>
          {TITLES.slice(0, 5).map((t) => <MediaTile key={t.title} item={t} blurred={blurred} />)}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--pa-space-3)' }}>
          {TITLES.slice(1, 4).map((t) => (
            <MediaRow key={t.title} item={t} reason="Same studio" why="Aurora Studios, like this one"
              blurred={blurred} action={<Button size="sm" icon="play">Play</Button>} />
          ))}
        </div>
        <Button size="sm" icon={blurred ? 'eye-closed' : 'eye'} onClick={() => setBlurred(!blurred)}>
          {blurred ? 'Reveal artwork' : 'Hide artwork'}
        </Button>
      </Section>

      <Section id="shell" title="Shell" note="sidebar, topbar, screen frame">
        <Screen height={520}>
          <Sidebar role="admin" user="kai" items={ADMIN_NAV} active="Requests" />
          <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
            <Topbar title="Requests" subtitle="8 open · 2 waiting on you" blurred={blurred} onToggleBlur={() => setBlurred(!blurred)} />
            <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', gap: 'var(--pa-space-4)', padding: '0 var(--pa-space-5) var(--pa-space-5)' }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0,1fr))', gap: 'var(--pa-space-4)' }}>
                <StatCard icon="film-strip" label="Titles" value="3,482" delta="+37 this week" />
                <StatCard icon="hard-drives" label="On disk" value="9.4 TB" delta="1.2 TB free" />
                <StatCard icon="tag" label="Tags" value="214" delta="12 groups" />
                <StatCard icon="users-three" label="People" value="4" delta="2 watching now" />
              </div>
              <Banner icon="wifi-slash" title="Lost the server" action={<Tag tone="accent">Reconnecting…</Tag>}>
                The live connection dropped 40 seconds ago. Numbers above are from the last update.
              </Banner>
              <ListRow icon="hand-pointing" title="Aurora 214 — Night Sessions" note="a guest · 4 releases found"
                tag={<Tag tone="accent">Needs approval</Tag>}
                actions={<Button size="sm" variant="primary" icon="check">Approve</Button>} />
            </div>
          </div>
        </Screen>
      </Section>

      <Section id="states" title="Quiet states">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0,1fr))', gap: 'var(--pa-space-4)' }}>
          <EmptyState kicker="Queue" title="Nothing to fetch right now">
            Grabs appear the moment you approve a request or a monitor finds something.
          </EmptyState>
          <EmptyState kicker="Quarantine" title="Nothing held back">
            Every file the last scan touched was matched or already in the library.
          </EmptyState>
          <EmptyState kicker="Feed" title="Not enough to go on yet"
            actions={<><Button size="sm" variant="primary">Browse the library</Button><Button size="sm">Watch shorts</Button></>}>
            The feed learns from what you finish, not what you open.
          </EmptyState>
        </div>
      </Section>

      <Section id="guest" title="Guest shell" note="same parts, different navigation">
        <Screen height={420}>
          <Sidebar role="guest" user="jonas" items={GUEST_NAV} active="Feed" />
          <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
            <Topbar title="For you" subtitle="From your own watching · owners hidden" blurred={blurred}
              onToggleBlur={() => setBlurred(!blurred)} connection="live" />
            <div className="pa-scroll" style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 'var(--pa-space-3)', padding: '0 var(--pa-space-5) var(--pa-space-5)' }}>
              {TITLES.slice(0, 4).map((t, i) => (
                <MediaRow key={t.title} item={t} reason={['94% match', '91% match', '88% match', '85% match'][i]}
                  why="low-light and handheld — your two most-watched tags" blurred={blurred} thumbWidth={112} />
              ))}
            </div>
          </div>
        </Screen>
      </Section>
    </main>
  );
}

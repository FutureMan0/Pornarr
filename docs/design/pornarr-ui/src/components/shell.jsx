import React from 'react';
import { Icon, Tag, Button, IconButton } from './primitives.jsx';

export function Sidebar({ brand = 'Pornarr', logoSrc, role = 'admin', user = 'kai', items = [], active, onSelect, footer }) {
  return (
    <nav style={{
      width: 228, flex: 'none', display: 'flex', flexDirection: 'column', gap: 'var(--pa-space-4)',
      padding: 'var(--pa-space-4)', background: 'var(--pa-bg-0)', boxShadow: 'inset -1px 0 0 rgba(236,238,244,0.06)'
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '0 8px' }}>
        {logoSrc
          ? <img className="pa-logo" src={logoSrc} alt={brand} style={{ height: 22, width: 'auto' }} />
          : <strong style={{ fontSize: 16, fontWeight: 500 }}>{brand}</strong>}
        <span style={{ marginLeft: 'auto', fontSize: 10, letterSpacing: '0.12em', color: 'var(--pa-text-muted)' }}>LOCAL</span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 9px', borderRadius: 'var(--pa-radius)', background: 'var(--pa-bg-2)' }}>
        <Icon name={role === 'admin' ? 'shield-check' : 'user'} color="var(--pa-accent-500)" />
        <span style={{ fontSize: 12 }}>{role === 'admin' ? 'Admin' : 'Guest'}</span>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--pa-text-muted)' }}>{user}</span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {items.map((it) => {
          const on = it.label === active;
          return (
            <button key={it.label} onClick={() => onSelect && onSelect(it.label)}
              style={{
                display: 'flex', alignItems: 'center', gap: 8, padding: '7px 9px',
                borderRadius: 'var(--pa-radius)', border: 0, cursor: 'pointer', font: 'inherit', fontSize: 13,
                textAlign: 'left',
                color: on ? 'var(--pa-accent-300)' : 'var(--pa-text-dim)',
                background: on ? 'color-mix(in srgb, var(--pa-accent-500) 14%, transparent)' : 'transparent'
              }}>
              <Icon name={it.icon} size={15} />
              <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.label}</span>
              {it.count && <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--pa-text-muted)' }}>{it.count}</span>}
            </button>
          );
        })}
      </div>
      <div style={{ marginTop: 'auto' }}>{footer}</div>
    </nav>
  );
}

const CONN = {
  live: { label: 'Live', color: 'var(--pa-text-muted)', dot: 'var(--pa-accent-500)', ring: true },
  reconnecting: { label: 'Reconnecting…', color: 'var(--pa-accent-300)', dot: 'var(--pa-accent-400)', ring: true },
  offline: { label: 'Offline', color: 'var(--pa-text-faint)', dot: 'var(--pa-bg-4)', ring: false }
};

export function Topbar({ title, subtitle, blurred = true, onToggleBlur, connection = 'live', search = 'Search titles, tags, performers…', actions }) {
  const c = CONN[connection] || CONN.live;
  return (
    <header style={{
      flex: 'none', display: 'flex', alignItems: 'center', gap: 'var(--pa-space-4)',
      padding: 'var(--pa-space-4) var(--pa-space-5)',
      background: 'linear-gradient(var(--pa-bg-0), var(--pa-bg-1))'
    }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 1, minWidth: 170 }}>
        <div style={{ fontSize: 19, fontWeight: 500, letterSpacing: '-0.02em' }}>{title}</div>
        {subtitle && <div style={{ fontSize: 11, color: 'var(--pa-text-muted)' }}>{subtitle}</div>}
      </div>
      {search && (
        <div style={{
          flex: 1, maxWidth: 640, display: 'flex', alignItems: 'center', gap: 9,
          minHeight: 36, padding: '0 12px', borderRadius: 'var(--pa-radius)',
          background: 'var(--pa-bg-2)', color: 'var(--pa-text-muted)', fontSize: 13
        }}>
          <Icon name="magnifying-glass" />{search}
        </div>
      )}
      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 'var(--pa-space-2)' }}>
        <Button size="sm" icon={blurred ? 'eye-closed' : 'eye'} onClick={onToggleBlur}>
          {blurred ? 'Art hidden' : 'Art shown'}
        </Button>
        {actions}
        <span title="Server connection" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: c.color, padding: '0 4px' }}>
          <span style={{
            width: 7, height: 7, borderRadius: '50%', background: c.dot,
            boxShadow: c.ring ? `0 0 0 3px color-mix(in srgb, ${c.dot} 22%, transparent)` : undefined
          }} />
          {c.label}
        </span>
      </div>
    </header>
  );
}

export function Screen({ children, width = 1280, height, style }) {
  return (
    <div style={{
      width, height, display: 'flex', borderRadius: 'var(--pa-radius-lg)', overflow: 'hidden',
      background: 'var(--pa-bg-1)', boxShadow: 'var(--pa-shadow-lg)', ...style
    }}>{children}</div>
  );
}

export function StatCard({ icon, label, value, delta }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2, padding: 'var(--pa-space-4)', borderRadius: 'var(--pa-radius)', background: 'var(--pa-bg-2)' }}>
      <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--pa-text-muted)' }}>
        {icon && <Icon name={icon} size={13} />}{label}
      </span>
      <span style={{ fontSize: 26, fontWeight: 500, letterSpacing: '-0.02em', fontVariantNumeric: 'tabular-nums' }}>{value}</span>
      {delta && <span style={{ fontSize: 11, color: 'var(--pa-accent-300)' }}>{delta}</span>}
    </div>
  );
}

export function ListRow({ icon, title, note, meta, tag, actions }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--pa-space-4)', padding: 'var(--pa-space-4)', borderRadius: 'var(--pa-radius)', background: 'var(--pa-bg-3)' }}>
      {icon && <Icon name={icon} size={18} color="var(--pa-accent-500)" />}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 3 }}>
        <span style={{ fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</span>
        {note && <span style={{ fontSize: 11, color: 'var(--pa-text-muted)' }}>{note}</span>}
      </div>
      {meta && <span style={{ flex: 'none', fontSize: 11, color: 'var(--pa-text-dim)' }}>{meta}</span>}
      {tag}
      {actions}
    </div>
  );
}

export function Banner({ icon = 'warning', title, children, action }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 14, padding: 14,
      borderRadius: 'var(--pa-radius)', background: 'var(--pa-bg-3)',
      boxShadow: 'inset 3px 0 0 var(--pa-accent-500)'
    }}>
      <Icon name={icon} size={20} color="var(--pa-accent-500)" />
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 3 }}>
        <span style={{ fontSize: 14 }}>{title}</span>
        <span style={{ fontSize: 12, color: 'var(--pa-text-muted)', textWrap: 'pretty' }}>{children}</span>
      </div>
      {action}
    </div>
  );
}

export function EmptyState({ kicker, title, children, actions }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 7, padding: 'var(--pa-space-5)', borderRadius: 'var(--pa-radius)', background: 'var(--pa-bg-2)' }}>
      {kicker && <span style={{ fontSize: 11, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--pa-text-muted)' }}>{kicker}</span>}
      <span style={{ fontSize: 18, fontWeight: 400 }}>{title}</span>
      <span style={{ fontSize: 12, color: 'var(--pa-text-muted)', textWrap: 'pretty' }}>{children}</span>
      {actions && <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>{actions}</div>}
    </div>
  );
}

export function ThemeSwitch({ value = 'rose', onChange }) {
  const themes = [['rose', '#d9629f'], ['amber', '#d9833f']];
  return (
    <div style={{ display: 'inline-flex', gap: 7, padding: 7, borderRadius: 10, background: 'var(--pa-bg-1)', boxShadow: 'var(--pa-shadow-sm)' }}>
      {themes.map(([id, dot]) => {
        const on = id === value;
        return (
          <button key={id} onClick={() => onChange && onChange(id)}
            style={{
              display: 'flex', alignItems: 'center', gap: 7, padding: '6px 12px', borderRadius: 'var(--pa-radius)',
              border: 0, cursor: 'pointer', font: 'inherit', fontSize: 13,
              color: on ? 'var(--pa-text)' : 'var(--pa-text-muted)',
              background: 'transparent', boxShadow: on ? `inset 0 0 0 1px ${dot}` : 'none'
            }}>
            <span style={{ width: 12, height: 12, borderRadius: '50%', background: dot }} />
            {id[0].toUpperCase() + id.slice(1)}
          </button>
        );
      })}
    </div>
  );
}

export { IconButton, Tag };

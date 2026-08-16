import React from 'react';

/* ── Icon ─────────────────────────────────────────────────────────── */
export function Icon({ name, weight = 'regular', size = 16, color, style, ...rest }) {
  const cls = weight === 'fill' ? `ph-fill ph-${name}` : `ph ph-${name}`;
  return <i className={cls} style={{ fontSize: size, color, lineHeight: 1, ...style }} {...rest} />;
}

/* ── Button ───────────────────────────────────────────────────────── */
const BTN = {
  primary: { color: 'var(--pa-accent-300)', boxShadow: 'inset 0 0 0 1px var(--pa-accent-500)', background: 'transparent' },
  secondary: { color: 'var(--pa-text-dim)', boxShadow: 'inset 0 0 0 1px rgba(236,238,244,0.16)', background: 'transparent' },
  ghost: { color: 'var(--pa-accent-300)', background: 'transparent' },
  tonal: { color: 'var(--pa-accent-200)', background: 'var(--pa-accent-800)' }
};

export function Button({ variant = 'secondary', icon, iconWeight, block, size = 'md', children, style, ...rest }) {
  const pad = size === 'sm' ? '0 10px' : '0 14px';
  const h = size === 'sm' ? 30 : 36;
  return (
    <button
      style={{
        display: block ? 'flex' : 'inline-flex', width: block ? '100%' : undefined,
        alignItems: 'center', justifyContent: 'center', gap: 7,
        height: h, padding: pad, borderRadius: 'var(--pa-radius)',
        border: 0, cursor: 'pointer', font: 'inherit',
        fontSize: size === 'sm' ? 12 : 13,
        ...BTN[variant], ...style
      }}
      {...rest}
    >
      {icon && <Icon name={icon} weight={iconWeight} size={15} />}
      {children}
    </button>
  );
}

export function IconButton({ icon, label, ...rest }) {
  return <Button aria-label={label} title={label} style={{ width: 36, padding: 0 }} icon={icon} {...rest} />;
}

/* ── Tag ──────────────────────────────────────────────────────────── */
const TAG = {
  accent: { color: 'var(--pa-accent-300)', background: 'color-mix(in srgb, var(--pa-accent-500) 16%, transparent)' },
  neutral: { color: 'var(--pa-text-dim)', background: 'var(--pa-bg-3)' },
  outline: { color: 'var(--pa-accent-300)', boxShadow: 'inset 0 0 0 1px rgba(236,238,244,0.16)' }
};

export function Tag({ tone = 'neutral', icon, children, style, ...rest }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '2px 8px', borderRadius: 'var(--pa-radius-sm)',
      fontSize: 11, whiteSpace: 'nowrap', ...TAG[tone], ...style
    }} {...rest}>
      {icon && <Icon name={icon} size={11} />}
      {children}
    </span>
  );
}

/* ── Card ─────────────────────────────────────────────────────────── */
export function Card({ title, action, children, scroll, style, ...rest }) {
  return (
    <section style={{
      display: 'flex', flexDirection: 'column', gap: 'var(--pa-space-3)',
      padding: 'var(--pa-space-4)', borderRadius: 'var(--pa-radius)',
      background: 'var(--pa-bg-2)', boxShadow: 'var(--pa-shadow-sm)',
      minHeight: scroll ? 0 : undefined, ...style
    }} {...rest}>
      {(title || action) && (
        <header style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {title && <h3 style={{ margin: 0, fontSize: 13, fontWeight: 500 }}>{title}</h3>}
          {action && <div style={{ marginLeft: 'auto' }}>{action}</div>}
        </header>
      )}
      {scroll ? <div className="pa-scroll" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--pa-space-3)', flex: 1 }}>{children}</div> : children}
    </section>
  );
}

/* ── Field / Input ────────────────────────────────────────────────── */
export function Field({ label, hint, children }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      {label && <span style={{ fontSize: 11, color: 'var(--pa-text-muted)' }}>{label}</span>}
      {children}
      {hint && <span style={{ fontSize: 11, color: 'var(--pa-text-faint)' }}>{hint}</span>}
    </label>
  );
}

export function Input({ icon, trailing, style, ...rest }) {
  return (
    <span style={{
      display: 'flex', alignItems: 'center', gap: 8,
      minHeight: 36, padding: '0 10px', borderRadius: 'var(--pa-radius)',
      background: 'var(--pa-bg-2)', boxShadow: 'inset 0 0 0 1px rgba(236,238,244,0.16)', ...style
    }}>
      {icon && <Icon name={icon} color="var(--pa-accent-500)" />}
      <input
        style={{
          flex: 1, minWidth: 0, border: 0, background: 'transparent',
          color: 'var(--pa-text)', font: 'inherit', fontSize: 13, outline: 'none'
        }}
        {...rest}
      />
      {trailing}
    </span>
  );
}

/* ── Toggle ───────────────────────────────────────────────────────── */
export function Toggle({ checked, onChange, label, note }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--pa-space-4)' }}>
      {(label || note) && (
        <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          {label && <span style={{ fontSize: 12 }}>{label}</span>}
          {note && <span style={{ fontSize: 11, color: 'var(--pa-text-muted)' }}>{note}</span>}
        </span>
      )}
      <button
        role="switch" aria-checked={!!checked} aria-label={label}
        onClick={() => onChange && onChange(!checked)}
        style={{
          marginLeft: 'auto', flex: 'none', width: 36, height: 20, padding: 2,
          display: 'flex', justifyContent: checked ? 'flex-end' : 'flex-start',
          borderRadius: 'var(--pa-radius-pill)', border: 0, cursor: 'pointer',
          background: checked ? 'var(--pa-accent-500)' : 'var(--pa-bg-4)'
        }}
      >
        <span style={{ width: 16, height: 16, borderRadius: '50%', background: checked ? 'var(--pa-bg-1)' : 'var(--pa-text-muted)' }} />
      </button>
    </div>
  );
}

/* ── Segmented ────────────────────────────────────────────────────── */
export function Segmented({ options, value, onChange }) {
  return (
    <div style={{ display: 'inline-flex', gap: 2, padding: 3, borderRadius: 'var(--pa-radius)', background: 'var(--pa-bg-2)' }}>
      {options.map((o) => {
        const v = typeof o === 'string' ? o : o.value;
        const label = typeof o === 'string' ? o : o.label;
        const on = v === value;
        return (
          <button key={v} onClick={() => onChange && onChange(v)}
            style={{
              border: 0, cursor: 'pointer', font: 'inherit', fontSize: 12,
              padding: '5px 11px', borderRadius: 6,
              color: on ? 'var(--pa-accent-300)' : 'var(--pa-text-muted)',
              background: on ? 'color-mix(in srgb, var(--pa-accent-500) 14%, transparent)' : 'transparent'
            }}>
            {label}
          </button>
        );
      })}
    </div>
  );
}

/* ── Stars ────────────────────────────────────────────────────────── */
export function Stars({ value = 0, size = 12, onRate }) {
  return (
    <span style={{ display: 'inline-flex', gap: 2 }}>
      {[1, 2, 3, 4, 5].map((n) => {
        const on = value >= n - 0.5;
        return (
          <Icon key={n} name="star" weight={on ? 'fill' : 'regular'} size={size}
            color={on ? 'var(--pa-accent-500)' : 'var(--pa-bg-4)'}
            onClick={onRate ? () => onRate(n) : undefined}
            style={{ cursor: onRate ? 'pointer' : undefined }} />
        );
      })}
    </span>
  );
}

/* ── ProgressBar ──────────────────────────────────────────────────── */
export function ProgressBar({ value = 0, height = 4 }) {
  return (
    <span style={{ display: 'block', height, borderRadius: height / 2, background: 'var(--pa-bg-4)', overflow: 'hidden' }}>
      <span style={{ display: 'block', height: '100%', width: `${value}%`, background: 'var(--pa-accent-500)' }} />
    </span>
  );
}

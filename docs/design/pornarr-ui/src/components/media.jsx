import React from 'react';
import { Icon, Tag, Stars } from './primitives.jsx';

/* Placeholder artwork anchored to the theme's art hue, so tiles stay in the
   accent's family in every theme instead of rotating into a rainbow. */
export function artFor(hue = 268) {
  const off = Math.round((((hue - 268 + 540) % 360) - 180) * 0.28);
  const h = (extra) => `calc(var(--pa-art-hue, 268deg) + ${off + extra}deg)`;
  return [
    `radial-gradient(58% 66% at 32% 34%, hsl(${h(0)} 34% 54%), transparent 72%)`,
    `radial-gradient(52% 62% at 74% 64%, hsl(${h(26)} 32% 44%), transparent 74%)`,
    'linear-gradient(155deg, var(--pa-bg-3), var(--pa-bg-00))'
  ].join(',');
}

export function Artwork({ hue, blurred = true, ratio = '16/10', radius = 6, strength = 'md', children }) {
  const blur = { lg: 'var(--pa-art-blur)', md: 'var(--pa-art-blur-md)', sm: 'var(--pa-art-blur-sm)' }[strength];
  return (
    <div style={{ position: 'relative', aspectRatio: ratio, borderRadius: radius, overflow: 'hidden', background: 'var(--pa-bg-1)' }}>
      <div style={{
        position: 'absolute', inset: '-12%', background: artFor(hue),
        filter: blurred
          ? `blur(${blur}) saturate(calc(1.25 * var(--pa-art-sat, 1)))`
          : 'blur(2px) saturate(calc(1.1 * var(--pa-art-sat, 1)))'
      }} />
      <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(to top, color-mix(in srgb, var(--pa-bg-00) 90%, transparent), transparent 62%)' }} />
      {blurred && (
        <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', color: 'rgba(236,238,244,0.5)', fontSize: 18 }}>
          <Icon name="eye-closed" size={18} />
        </div>
      )}
      {children}
    </div>
  );
}

export function MediaTile({ item, blurred = true, ratio = '16/10' }) {
  const { title, meta, dur, res, tags = 0, rating = 0, comments = 0, hue, progress = '0%' } = item;
  return (
    <article style={{ minWidth: 0, borderRadius: 'var(--pa-radius)', overflow: 'hidden', background: 'var(--pa-bg-2)', boxShadow: 'var(--pa-shadow-sm)' }}>
      <Artwork hue={hue} blurred={blurred} ratio={ratio} radius={0}>
        {res && <span style={{ position: 'absolute', left: 7, top: 7, fontSize: 10, padding: '2px 6px', borderRadius: 5, background: 'color-mix(in srgb, var(--pa-bg-00) 76%, transparent)', color: 'var(--pa-text-dim)' }}>{res}</span>}
        {dur && <span style={{ position: 'absolute', right: 7, bottom: 7, fontSize: 10, padding: '2px 6px', borderRadius: 5, background: 'color-mix(in srgb, var(--pa-bg-00) 82%, transparent)', fontVariantNumeric: 'tabular-nums' }}>{dur}</span>}
        <span style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: 2, background: 'rgba(236,238,244,0.12)' }}>
          <span style={{ display: 'block', height: '100%', width: progress, background: 'var(--pa-accent-500)' }} />
        </span>
      </Artwork>
      <div style={{ padding: '8px 9px 10px', display: 'flex', flexDirection: 'column', gap: 5 }}>
        <div style={{ fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--pa-text-muted)', minWidth: 0 }}>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{meta}</span>
          <span style={{ marginLeft: 'auto', flex: 'none', display: 'flex', alignItems: 'center', gap: 3, color: 'var(--pa-accent-300)' }}>
            <Icon name="tag" size={11} />{tags}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <Stars value={rating} size={10} />
          <span style={{ fontSize: 10, color: 'var(--pa-text-muted)', fontVariantNumeric: 'tabular-nums' }}>{rating ? rating.toFixed(1) : '—'}</span>
          <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 3, fontSize: 10, color: 'var(--pa-text-faint)' }}>
            <Icon name="chat-circle" size={10} />{comments}
          </span>
        </div>
      </div>
    </article>
  );
}

export function MediaRow({ item, reason, why, blurred = true, thumbWidth = 128, ratio = '16/10', action }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--pa-space-4)', padding: 8, borderRadius: 'var(--pa-radius)', background: 'var(--pa-bg-3)', minWidth: 0 }}>
      <div style={{ width: thumbWidth, flex: 'none' }}>
        <Artwork hue={item.hue} blurred={blurred} ratio={ratio} strength="sm" />
      </div>
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 3 }}>
        <span style={{ fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.title}</span>
        <span style={{ fontSize: 11.5, color: 'var(--pa-text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {[item.meta, item.dur, item.res].filter(Boolean).join(' · ')}
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
          <Stars value={item.rating || 0} size={10} />
          {reason && <Tag tone="outline">{reason}</Tag>}
          {why && <span style={{ fontSize: 11, color: 'var(--pa-text-faint)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{why}</span>}
        </span>
      </div>
      {action}
    </div>
  );
}

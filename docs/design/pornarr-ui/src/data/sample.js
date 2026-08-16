export const TITLES = [
  ['Aurora 214 — Night Sessions', 'Aurora Studios', '24:05', '2160p', 9, 268, '34%', 4.5, 12],
  ['Blue Hour, Part 2', 'Meridian', '18:42', '1080p', 6, 214, '0%', 3.5, 4],
  ['Static Garden', 'Independent', '41:10', '1080p', 12, 300, '78%', 5, 21],
  ['Low Tide', 'Aurora Studios', '12:36', '2160p', 4, 190, '0%', 3, 2],
  ['Room 9', 'Nightfall', '32:18', '1080p', 8, 330, '12%', 4, 8],
  ['Paper Lantern', 'Meridian', '27:54', '1080p', 7, 250, '0%', 4.5, 6]
].map(([title, meta, dur, res, tags, hue, progress, rating, comments]) =>
  ({ title, meta, dur, res, tags, hue, progress, rating, comments }));

export const ADMIN_NAV = [
  { label: 'Dashboard', icon: 'gauge' },
  { label: 'Scan & Import', icon: 'magnifying-glass', count: '12' },
  { label: 'Requests', icon: 'hand-pointing', count: '8' },
  { label: 'Monitors', icon: 'crosshair', count: '5' },
  { label: 'Queue', icon: 'arrow-circle-down', count: '4' },
  { label: 'Tags', icon: 'tag' },
  { label: 'Settings', icon: 'gear-six' }
];

export const GUEST_NAV = [
  { label: 'Feed', icon: 'broadcast', count: '7' },
  { label: 'Continue', icon: 'play-circle', count: '3' },
  { label: 'My library', icon: 'lock-simple', count: '1,204' },
  { label: 'Search all', icon: 'magnifying-glass' },
  { label: 'Shorts', icon: 'device-mobile', count: '412' },
  { label: 'Watchlist', icon: 'bookmark-simple' }
];

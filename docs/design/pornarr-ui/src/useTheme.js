import { useEffect, useState } from 'react';

/** Reads and writes data-theme on <html>, persisted per browser. */
export default function useTheme(initial = 'rose') {
  const [theme, setTheme] = useState(() => localStorage.getItem('pornarr-theme') || initial);
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('pornarr-theme', theme);
  }, [theme]);
  return [theme, setTheme];
}

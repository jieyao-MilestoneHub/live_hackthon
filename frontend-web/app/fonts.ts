// Typography for 浪 LIVE (Neon Night Wave). Self-hosted at build via next/font
// — no runtime external font requests, works with output: 'export'.
//   Display : Bricolage Grotesque — characterful grotesque for H1 / titles / logotype
//   Body/UI : Manrope           — clean geometric sans for all UI text
//   Data    : JetBrains Mono    — timecodes (ms), scores, ids, eyebrows
// CJK falls back to the system stack declared in globals.css.

import { Bricolage_Grotesque, Manrope, JetBrains_Mono } from 'next/font/google';

export const display = Bricolage_Grotesque({
  subsets: ['latin'],
  weight: ['600', '700', '800'],
  variable: '--font-display',
  display: 'swap',
});

export const body = Manrope({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-body',
  display: 'swap',
});

export const mono = JetBrains_Mono({
  subsets: ['latin'],
  weight: ['400', '500', '700'],
  variable: '--font-mono',
  display: 'swap',
});

/** Combined className to apply all three font CSS variables on <html>. */
export const fontVars = `${display.variable} ${body.variable} ${mono.variable}`;

import type { Metadata } from 'next';
import Link from 'next/link';
import './globals.css';
import { fontVars } from './fonts';
import Brand from '@/components/Brand';

export const metadata: Metadata = {
  title: '浪 LIVE — AI 直播高光剪輯',
  description: '上傳直播錄影，AI 找出情緒高峰、自動組出 60 秒內的精華短片，時間軸交給你微調。',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-TW" className={fontVars}>
      <body>
        <header className="topbar">
          <div className="topbar__inner">
            <Link href="/" aria-label="浪 LIVE 首頁" style={{ textDecoration: 'none' }}>
              <Brand />
            </Link>
            <nav className="topbar__nav">
              <span className="mono">直播高光剪輯</span>
              <Link href="/" className="btn btn--ghost btn--sm">
                進入編輯
              </Link>
            </nav>
          </div>
        </header>

        {children}

        <footer className="footer">
          <div className="shell">
            <span>
              <Brand /> · AI 直播高光剪輯
            </span>
            <span className="mono muted">M1 · Project / ms · openapi v0.2.0</span>
          </div>
        </footer>
      </body>
    </html>
  );
}

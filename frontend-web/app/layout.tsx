import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: '浪 LIVE — AI 高光剪輯',
  description: 'AI 直播高光自動剪輯：上傳影片，取得精彩短片。',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-TW">
      <body>
        <div className="container">
          <div className="brand">
            <span className="brand-mark">
              <span className="wave">浪</span> LIVE
            </span>
          </div>
          <p className="subtitle">AI 直播高光自動剪輯 · 影片編輯器</p>
          {children}
        </div>
      </body>
    </html>
  );
}

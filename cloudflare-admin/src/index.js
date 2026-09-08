// HTMLもWorkerへ同梱します。Static Assetsを使わないため、
// Cloudflareが検証したログイン情報（ctx.access）を直接受け取れます。
import html from '../public/index.html';
import { createApp } from './app.js';

export default createApp(html);

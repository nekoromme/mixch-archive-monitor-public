// 管理画面とログイン画面をWorkerへ同梱します。
import html from '../public/index.html';
import loginHtml from '../public/login.html';
import { createApp } from './app.js';

import { scheduled } from './scheduler.js';
export default { ...createApp(html, undefined, loginHtml), scheduled };


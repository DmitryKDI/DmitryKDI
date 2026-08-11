import 'dotenv/config';
import express from 'express';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createDataAdapter } from './data/adapter.js';
import { createDashboardRouter } from './routes/dashboard.js';
import { createChatRouter } from './routes/chat.js';
import { createSalesRouter } from './routes/sales.js';
import { createReportsRouter } from './routes/reports.js';
import { createDocumentsRouter } from './routes/documents.js';
import { REPORTS_DIR } from './reports/excelReport.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = process.env.PORT || 4000;

fs.mkdirSync(REPORTS_DIR, { recursive: true });

const adapter = createDataAdapter();

const app = express();
app.use(express.json());

app.use('/api/dashboard', createDashboardRouter(adapter));
app.use('/api/chat', createChatRouter(adapter));
app.use('/api/sales', createSalesRouter(adapter));
app.use('/api/reports', createReportsRouter());
app.use('/api', createDocumentsRouter(adapter));

// В проде (после `npm run build` в client/) отдаём собранный React-бандл с того
// же порта. В dev-режиме (npm run dev) фронтенд крутится отдельно на Vite dev
// server и проксирует /api сюда — client/dist тогда просто не существует.
const clientDist = path.join(__dirname, '..', 'client', 'dist');
const clientIndexHtml = path.join(clientDist, 'index.html');
if (fs.existsSync(clientDist)) {
  app.use(express.static(clientDist));
}

app.use((req, res, next) => {
  if (req.method !== 'GET' || req.path.startsWith('/api/')) return next();
  if (!fs.existsSync(clientIndexHtml)) return next();
  res.sendFile(clientIndexHtml);
});

app.listen(PORT, () => {
  const mode = (process.env.DATA_ADAPTER_MODE || 'mock').toLowerCase();
  console.log(`Бизнес-ассистент запущен: http://localhost:${PORT} (адаптер данных: ${mode})`);
});

import { Router } from 'express';
import path from 'node:path';
import { REPORTS_DIR } from '../reports/excelReport.js';

export function createReportsRouter() {
  const router = Router();

  router.get('/:filename', (req, res) => {
    const safeName = path.basename(req.params.filename);
    if (!safeName || safeName !== req.params.filename || !safeName.endsWith('.xlsx')) {
      return res.status(400).json({ error: 'Некорректное имя файла' });
    }
    const filePath = path.join(REPORTS_DIR, safeName);
    if (!filePath.startsWith(REPORTS_DIR)) {
      return res.status(400).json({ error: 'Некорректный путь' });
    }
    res.download(filePath, safeName, (err) => {
      if (err && !res.headersSent) {
        res.status(404).json({ error: 'Отчёт не найден — возможно, сервер был перезапущен' });
      }
    });
  });

  return router;
}

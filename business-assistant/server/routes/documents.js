import { Router } from 'express';
import multer from 'multer';

// memoryStorage — файл приходит как Buffer в req.files[].buffer, адаптер сам
// решает, писать ли его на диск (dbAdapter.js) или просто прочитать размер
// (mockAdapter.js, ничего не персистит). См. server/data/adapter.js — интерфейс
// намеренно не завязан на то, что за multer-хранилище использует роут.
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 20 * 1024 * 1024, files: 10 },
});

export function createDocumentsRouter(adapter) {
  const router = Router();

  router.get('/deals/:dealId/documents', async (req, res) => {
    try {
      const documents = await adapter.listDocuments(req.params.dealId);
      res.json({ documents });
    } catch (err) {
      res.status(404).json({ error: err.message });
    }
  });

  // Мгновенная генерация без вложений: type = kp | contract | act | invoice
  router.post('/deals/:dealId/documents/generate', async (req, res) => {
    try {
      const { type } = req.body || {};
      const document = await adapter.createGeneratedDocument({ dealId: req.params.dealId, type });
      res.status(201).json({ document });
    } catch (err) {
      res.status(400).json({ error: err.message });
    }
  });

  // Смета по замерам: { area, level }
  router.post('/deals/:dealId/documents/smeta', async (req, res) => {
    try {
      const { area, level } = req.body || {};
      const document = await adapter.createSmetaDocument({ dealId: req.params.dealId, area, level });
      res.status(201).json({ document });
    } catch (err) {
      res.status(400).json({ error: err.message });
    }
  });

  // Требует вложений: type = design | other, multipart/form-data, поле "files"
  router.post('/deals/:dealId/documents/upload', upload.array('files', 10), async (req, res) => {
    try {
      const { type } = req.body || {};
      const document = await adapter.createFileDocument({
        dealId: req.params.dealId,
        type,
        files: req.files || [],
      });
      res.status(201).json({ document });
    } catch (err) {
      res.status(400).json({ error: err.message });
    }
  });

  router.delete('/documents/:id', async (req, res) => {
    try {
      const document = await adapter.deleteDocument(req.params.id);
      res.json({ document });
    } catch (err) {
      res.status(404).json({ error: err.message });
    }
  });

  // Скачивание сгенерированного текстового/CSV документа (КП, смета, договор, акт, счёт).
  router.get('/documents/:id/download', async (req, res) => {
    try {
      const { filename, data } = await adapter.getDocumentContent(req.params.id);
      res.setHeader('Content-Disposition', `attachment; filename="${encodeURIComponent(filename)}"`);
      res.setHeader('Content-Type', 'text/plain; charset=utf-8');
      res.send(data);
    } catch (err) {
      res.status(404).json({ error: err.message });
    }
  });

  // Скачивание конкретного вложенного файла (дизайн-проект, другой документ).
  router.get('/documents/files/:fileId/download', async (req, res) => {
    try {
      const { filename, mimeType, data } = await adapter.getDocumentFile(req.params.fileId);
      res.setHeader('Content-Disposition', `attachment; filename="${encodeURIComponent(filename)}"`);
      res.setHeader('Content-Type', mimeType);
      res.send(data);
    } catch (err) {
      res.status(404).json({ error: err.message });
    }
  });

  return router;
}

import { Router } from 'express';

/**
 * Раздел "Продажи" — ролевой доступ (демо-режим, без реальной авторизации).
 *
 * ⚠️ В демо роль передаётся параметром запроса без проверки — это НАМЕРЕННОЕ
 * упрощение только для прототипа (нет логина, см. README → Этап 6). В боевой
 * версии проверка "имеет ли текущий пользователь право смотреть контакты
 * менеджера X" обязана происходить на сервере на основе реальной сессии/токена,
 * а не доверять клиенту.
 */
export function createSalesRouter(adapter) {
  const router = Router();

  router.get('/managers', async (req, res) => {
    try {
      const managers = await adapter.listManagers();
      res.json({ managers });
    } catch (err) {
      console.error('sales/managers error', err);
      res.status(500).json({ error: err.message });
    }
  });

  router.get('/overview', async (req, res) => {
    try {
      const periodDays = Number(req.query.periodDays) || 30;
      const overview = await adapter.getSalesOverview({ periodDays });
      res.json(overview);
    } catch (err) {
      console.error('sales/overview error', err);
      res.status(500).json({ error: err.message });
    }
  });

  router.get('/contacts', async (req, res) => {
    try {
      const manager = req.query.manager;
      if (!manager) return res.status(400).json({ error: 'manager query param is required' });
      const contacts = await adapter.getManagerContacts(String(manager));
      res.json({ manager, contacts });
    } catch (err) {
      console.error('sales/contacts error', err);
      res.status(404).json({ error: err.message });
    }
  });

  return router;
}

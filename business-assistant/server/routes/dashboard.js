import { Router } from 'express';

export function createDashboardRouter(adapter) {
  const router = Router();

  router.get('/', async (req, res) => {
    try {
      const [metrics, deals, callHistory] = await Promise.all([
        adapter.getDashboardMetrics(),
        adapter.listDeals({}),
        adapter.listCallHistory().catch(() => []), // может отсутствовать до подключения LPTracker
      ]);
      res.json({ metrics, deals, callHistory });
    } catch (err) {
      console.error('dashboard error', err);
      res.status(500).json({ error: err.message });
    }
  });

  return router;
}

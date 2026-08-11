// Заготовка под Этап 5 ("Голосовые звонки — интеграция с LPTracker").
// Пока НЕ подключена ни к одному function calling инструменту ассистента —
// это точка расширения, а не рабочий код.
//
// Перед реализацией:
//  1. Уточнить актуальную документацию LPTracker (эндпоинты и формат авторизации
//     могут отличаться в зависимости от тарифа/версии API аккаунта).
//  2. Продумать сценарии: напоминание клиенту, прозвон по новой заявке,
//     уточнение статуса у подрядчика (см. бриф, модуль 5).
//  3. Юридический момент (РФ): возможна обязательная необходимость сообщать
//     собеседнику, что звонок совершает ИИ, а не живой оператор — уточнить
//     у LPTracker их позицию/настройки перед боевым запуском.
//  4. Webhook от LPTracker с результатом звонка (транскрипт/исход) должен
//     обновлять сделку в Битрикс24 — например, через createTask()/updateDealStage()
//     из bitrix24Adapter.js, чтобы результат звонка сразу попадал в CRM.

const API_KEY = process.env.LPTRACKER_API_KEY;

export function createLpTrackerAdapter() {
  return {
    /**
     * Инициировать звонок клиенту по сделке.
     * @param {{dealId: string, phone: string, topic: string}} params
     */
    async initiateCall({ dealId, phone, topic }) {
      if (!API_KEY) {
        throw new Error(
          'LPTRACKER_API_KEY не задан — интеграция со звонками ещё не подключена (см. Этап 5 в README)',
        );
      }
      // TODO: реальный вызов LPTracker REST API. Пример структуры (уточнить по
      // актуальной документации LPTracker перед использованием):
      //
      //   const res = await fetch('https://direct.lptracker.ru/api/call/init', {
      //     method: 'POST',
      //     headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${API_KEY}` },
      //     body: JSON.stringify({ deal_id: dealId, phone, topic }),
      //   });
      //   return res.json();
      throw new Error('initiateCall не реализован — заготовка для Этапа 5');
    },

    /**
     * Обработчик webhook с результатом звонка от LPTracker.
     * Должен вызываться из отдельного POST-роута (не входит в этот демо-билд),
     * после чего результат нужно записать в Битрикс24 через bitrix24Adapter.js.
     */
    async handleCallResultWebhook(payload) {
      // TODO: провалидировать подпись/секрет вебхука LPTracker, распарсить
      // транскрипт/исход и записать в CRM.
      throw new Error('handleCallResultWebhook не реализован — заготовка для Этапа 5');
    },
  };
}

import { useEffect, useState } from 'react';
import { api } from '../api.js';

function formatMoney(n) {
  if (n === null || n === undefined) return '—';
  return new Intl.NumberFormat('ru-RU').format(n) + ' ₽';
}

const GENERATE_TYPES = [
  { type: 'kp', label: '+ КП' },
  { type: 'contract', label: '+ Договор' },
  { type: 'act', label: '+ Акт приёмки' },
  { type: 'invoice', label: '+ Счёт' },
];

const FILE_TYPES = {
  design: { label: '+ Дизайн-проект', hint: 'Прикрепите файлы (планировка, референсы) — без них создать дизайн-проект нельзя.' },
  other: { label: '+ Другой документ', hint: 'Прикрепите один или несколько файлов (паспорт, страховой полис, скан подписанного документа и т.п.).' },
};

const SMETA_LEVELS = [
  { key: 'econom', label: 'Эконом' },
  { key: 'standard', label: 'Стандарт' },
  { key: 'premium', label: 'Премиум' },
];

export default function DealDetail({ deal, onClose }) {
  const [documents, setDocuments] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [openForm, setOpenForm] = useState(null); // 'smeta' | 'design' | 'other' | null
  const [smetaArea, setSmetaArea] = useState('');
  const [smetaLevel, setSmetaLevel] = useState('standard');
  const [pendingFiles, setPendingFiles] = useState([]);

  function reload() {
    api
      .getDocuments(deal.id)
      .then((d) => setDocuments(d.documents))
      .catch((err) => setError(err.message));
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deal.id]);

  async function handleGenerate(type) {
    setBusy(true);
    setError(null);
    try {
      await api.generateDocument(deal.id, type);
      reload();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleSmetaSubmit() {
    if (!smetaArea) return;
    setBusy(true);
    setError(null);
    try {
      await api.generateSmeta(deal.id, Number(smetaArea), smetaLevel);
      setOpenForm(null);
      setSmetaArea('');
      reload();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleFileSubmit(type) {
    if (!pendingFiles.length) return;
    setBusy(true);
    setError(null);
    try {
      await api.uploadDocument(deal.id, type, pendingFiles);
      setOpenForm(null);
      setPendingFiles([]);
      reload();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(id) {
    setBusy(true);
    setError(null);
    try {
      await api.deleteDocument(id);
      reload();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="overlay" role="dialog" aria-modal="true">
      <div className="overlay-backdrop" onClick={onClose} />
      <div className="overlay-panel">
        <div className="overlay-header">
          <div>
            <div className="overlay-title">{deal.address}</div>
            <div className="overlay-sub">
              {deal.clientName} · {deal.type} · {deal.stage}
            </div>
          </div>
          <button className="overlay-close" onClick={onClose} aria-label="Закрыть">
            ✕
          </button>
        </div>

        <div className="overlay-body">
          <h2>Информация об объекте</h2>
          <div className="card">
            <div className="detail-row">
              <span>Телефон</span>
              <span>{deal.clientPhone}</span>
            </div>
            <div className="detail-row">
              <span>Бюджет</span>
              <span>{formatMoney(deal.budget)}</span>
            </div>
            <div className="detail-row">
              <span>Менеджер</span>
              <span>{deal.manager}</span>
            </div>
            <div className="detail-row">
              <span>Создана</span>
              <span>{deal.createdDate}</span>
            </div>
          </div>

          <h2>Документы</h2>
          {error && <div className="error-banner">{error}</div>}
          <div className="card doc-list-card">
            {!documents ? (
              <div className="loading">Загрузка…</div>
            ) : documents.length === 0 ? (
              <div className="empty">Пока не создано ни одного документа.</div>
            ) : (
              documents.map((doc) => (
                <div className="doc-card" key={doc.id}>
                  <div className="doc-card-main">
                    <div className="doc-title">{doc.title}</div>
                    <div className="doc-sub">
                      {new Date(doc.createdDate).toLocaleDateString('ru-RU')}
                      {doc.files.length > 0 && ` · ${doc.files.map((f) => f.name).join(', ')}`}
                      {doc.meta && ` · ${doc.meta.area} м² · ${doc.meta.levelLabel} · ${formatMoney(doc.meta.total)}`}
                    </div>
                  </div>
                  <span className={`stage-pill${doc.status === 'готово' ? ' done' : ''}`}>{doc.status}</span>
                  {doc.downloadable && (
                    <a
                      className="doc-icon-btn"
                      href={api.documentDownloadUrl(doc.id)}
                      title="Скачать"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      ⬇
                    </a>
                  )}
                  {doc.files.map((f) => (
                    <a
                      key={f.id || f.name}
                      className="doc-icon-btn"
                      href={f.id ? api.documentFileDownloadUrl(f.id) : undefined}
                      title={f.id ? `Скачать ${f.name}` : `${f.name} — скачивание недоступно в mock-режиме`}
                      aria-disabled={!f.id}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      ⬇
                    </a>
                  ))}
                  <button className="doc-icon-btn" onClick={() => handleDelete(doc.id)} disabled={busy} title="Удалить">
                    ✕
                  </button>
                </div>
              ))
            )}
          </div>

          <div className="doc-add-menu">
            {GENERATE_TYPES.map((opt) => (
              <button key={opt.type} disabled={busy} onClick={() => handleGenerate(opt.type)}>
                {opt.label}
              </button>
            ))}
            <button
              className={openForm === 'smeta' ? 'active' : ''}
              onClick={() => setOpenForm(openForm === 'smeta' ? null : 'smeta')}
            >
              + Смета
            </button>
            {Object.entries(FILE_TYPES).map(([type, cfg]) => (
              <button
                key={type}
                className={openForm === type ? 'active' : ''}
                onClick={() => setOpenForm(openForm === type ? null : type)}
              >
                {cfg.label}
              </button>
            ))}
          </div>

          {openForm === 'smeta' && (
            <div className="design-form">
              <p className="hint">Укажите площадь объекта — смета считается автоматически по ставкам за м² (демо-калибровка, не прайс-лист).</p>
              <div className="measure-row">
                <input
                  type="number"
                  min="1"
                  step="0.1"
                  placeholder="Площадь, м²"
                  value={smetaArea}
                  onChange={(e) => setSmetaArea(e.target.value)}
                />
                <select value={smetaLevel} onChange={(e) => setSmetaLevel(e.target.value)}>
                  {SMETA_LEVELS.map((l) => (
                    <option key={l.key} value={l.key}>
                      {l.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="design-form-actions">
                <button className="send-btn" disabled={!smetaArea || busy} onClick={handleSmetaSubmit}>
                  Рассчитать и создать
                </button>
                <button
                  className="icon-btn"
                  onClick={() => {
                    setOpenForm(null);
                    setSmetaArea('');
                  }}
                >
                  Отмена
                </button>
              </div>
            </div>
          )}

          {(openForm === 'design' || openForm === 'other') && (
            <div className="design-form">
              <p className="hint">{FILE_TYPES[openForm].hint}</p>
              <input type="file" multiple onChange={(e) => setPendingFiles(Array.from(e.target.files))} />
              {pendingFiles.length > 0 && (
                <div className="file-chips">
                  {pendingFiles.map((f, i) => (
                    <span className="file-chip" key={i}>
                      {f.name}
                    </span>
                  ))}
                </div>
              )}
              <div className="design-form-actions">
                <button className="send-btn" disabled={!pendingFiles.length || busy} onClick={() => handleFileSubmit(openForm)}>
                  Создать
                </button>
                <button
                  className="icon-btn"
                  onClick={() => {
                    setOpenForm(null);
                    setPendingFiles([]);
                  }}
                >
                  Отмена
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

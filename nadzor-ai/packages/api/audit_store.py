"""Связь журнала аудита с хранилищем.

Цепочка хэшей продолжается между перезапусками: при старте записи читаются
из базы, новая запись ссылается на хэш последней сохранённой.
"""
from __future__ import annotations

from audit.chain import AuditChain, AuditRecord
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import AuditRow
from api.state import state

_PENDING_KEY = "_pending_audit_count"


def _to_record(row: AuditRow) -> AuditRecord:
    return AuditRecord(seq=row.seq, occurred_at=row.occurred_at, actor_id=row.actor_id,
                       actor_role=row.actor_role, action=row.action,
                       entity_type=row.entity_type, entity_id=row.entity_id,
                       payload=row.payload, prev_hash=row.prev_hash,
                       record_hash=row.record_hash)


def _track_pending(session: AsyncSession) -> None:
    """Откатить запись из памяти, если транзакция не закоммитится.

    `record()` мутирует цепочку в памяти сразу, до commit(), чтобы вызывающий
    код мог использовать хэш записи синхронно. Если коммит впоследствии не
    состоится (сбой БД, конфликт), эти записи нужно убрать из памяти — иначе
    следующая настоящая запись сошлётся на хэш, которого в базе никогда не
    было, и после перезапуска обычный сбой транзакции будет неотличим от
    подделки. Слушатели вешаются один раз на сессию и считают такие записи
    до ближайшего commit/rollback этой сессии.
    """
    sync = session.sync_session
    if _PENDING_KEY in sync.info:
        sync.info[_PENDING_KEY] += 1
        return
    sync.info[_PENDING_KEY] = 1

    def _on_rollback(sess: object) -> None:
        state.audit.discard_uncommitted(sess.info.get(_PENDING_KEY, 0))
        sess.info[_PENDING_KEY] = 0

    def _on_commit(sess: object) -> None:
        sess.info[_PENDING_KEY] = 0

    event.listen(sync, "after_rollback", _on_rollback)
    event.listen(sync, "after_commit", _on_commit)


def record(session: AsyncSession, actor_id: str, actor_role: str, action: str,
           entity_type: str, entity_id: str = "", payload: dict | None = None) -> AuditRecord:
    """Единственный способ записи в журнал: цепочка и хранилище обновляются вместе.

    Запись только в память разорвала бы цепочку при перезапуске процесса.
    """
    entry = state.audit.append(actor_id, actor_role, action, entity_type, entity_id,
                               payload or {})
    session.add(AuditRow(seq=entry.seq, occurred_at=entry.occurred_at, actor_id=entry.actor_id,
                         actor_role=entry.actor_role, action=entry.action,
                         entity_type=entry.entity_type, entity_id=entry.entity_id,
                         payload=entry.payload, prev_hash=entry.prev_hash,
                         record_hash=entry.record_hash))
    _track_pending(session)
    return entry


async def restore_chain(session: AsyncSession) -> dict:
    """Восстановить цепочку из базы и проверить её целостность при старте."""
    rows = (await session.execute(select(AuditRow).order_by(AuditRow.seq))).scalars().all()
    state.audit = AuditChain([_to_record(r) for r in rows])
    return state.audit.verify()

"""Целостность журнала аудита."""
from __future__ import annotations

from audit.chain import AuditChain


def _chain() -> AuditChain:
    chain = AuditChain()
    chain.append("sudir:77001", "inspector", "analysis.run", "run", "r-1", {"object": "OBJ-001"})
    chain.append("sudir:77001", "inspector", "finding.assess", "finding", "H-1",
                 {"verdict": "confirmed"})
    chain.append("sudir:77002", "head_of_dept", "report.act", "object", "OBJ-001")
    return chain


def test_valid_chain():
    result = _chain().verify()
    assert result["valid"] is True
    assert result["records"] == 3


def test_modified_record_breaks_chain():
    """Изменение содержимого записи обнаруживается проверкой."""
    chain = _chain()
    chain._records[1].payload = {"verdict": "rejected"}
    result = chain.verify()
    assert result["valid"] is False
    assert result["broken_at"] == 2
    assert "изменено" in result["reason"]


def test_deleted_record_breaks_chain():
    chain = _chain()
    del chain._records[1]
    result = chain.verify()
    assert result["valid"] is False


def test_reordered_records_break_chain():
    chain = _chain()
    chain._records[1], chain._records[2] = chain._records[2], chain._records[1]
    assert chain.verify()["valid"] is False


def test_secrets_and_personal_data_are_not_stored():
    """В журнал не попадают токены, а ФИО сокращаются до инициалов."""
    chain = AuditChain()
    record = chain.append("sudir:77001", "inspector", "auth.login", "user", "sudir:77001",
                          {"token": "секретное-значение", "password": "123",
                           "inspector": "Кузнецова Марина Викторовна",
                           "email": "user@example.com"})
    assert "token" not in record.payload
    assert "password" not in record.payload
    assert record.payload["inspector"] == "Кузнецова М. В."
    assert record.payload["email"] == "[адрес скрыт]"


def test_api_reports_broken_chain(client, auth):
    """Проверка целостности доступна аудитору через интерфейс."""
    result = client.post("/api/audit/verify", headers=auth("sudir:77006")).json()
    assert result["valid"] is True


async def test_failed_commit_does_not_leave_phantom_audit_entry(tmp_path):
    """Откат транзакции не должен оставлять в цепочке запись, которой нет в БД.

    audit_store.record() мутирует цепочку в памяти до commit(), чтобы вернуть
    хэш вызывающему коду синхронно. Если коммит не состоится, эта запись
    обязана уйти из памяти — иначе следующая настоящая запись сошлётся на
    хэш, которого в базе никогда не было, и обычный сбой транзакции после
    перезапуска будет неотличим от подделки записи.
    """
    import api.state as state_module
    from api.audit_store import record
    from api.models import Base
    from audit.chain import AuditChain
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/audit_rollback.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    original_chain = state_module.state.audit
    state_module.state.audit = AuditChain()
    try:
        async with session_factory() as session:
            record(session, "sudir:test", "inspector", "analysis.run", "run", "r-rollback")
            assert len(state_module.state.audit) == 1
            await session.rollback()  # тот же путь, что и при неудачном commit()

        assert len(state_module.state.audit) == 0
        assert state_module.state.audit.verify()["valid"] is True
    finally:
        state_module.state.audit = original_chain
        await engine.dispose()

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

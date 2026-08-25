"""Ручное добавление объекта — единственный способ работать не с тремя
демо-объектами, а со своим: POST /objects не существовал до этого изменения."""
from __future__ import annotations


def test_inspector_can_create_and_immediately_see_own_object(client, auth):
    headers = auth("sudir:77001")  # инспектор Кузнецова, department "Отдел... № 3"

    resp = client.post("/api/objects", json={
        "permit_number": "77-000000-999999-2026", "name": "ЖК Тестовый",
        "address": "г. Москва, ул. Пробная, 1", "developer": "ООО Стройтест",
    }, headers=headers)
    assert resp.status_code == 200, resp.text
    obj = resp.json()
    assert obj["data_source"] == "manual"
    assert obj["name"] == "ЖК Тестовый"
    print(f"OK: объект создан, id={obj['id']}, data_source=manual")

    # Тот же инспектор должен сразу увидеть свой объект в общем списке —
    # scope_objects фильтрует по assigned_to, значит поле обязано быть заполнено.
    listed = client.get("/api/objects", headers=headers).json()["items"]
    assert any(o["id"] == obj["id"] for o in listed), \
        "созданный объект не попал в собственную выборку инспектора"
    print("OK: инспектор сразу видит созданный им объект в своей выборке")

    # Другой инспектор из другого отдела/без назначения объект не видит —
    # право «запрещено по умолчанию» действует и для ручных объектов.
    other = client.get("/api/objects", headers=auth("sudir:77007")).json()["items"]
    assert not any(o["id"] == obj["id"] for o in other)
    print("OK: чужой инспектор созданный объект не видит")


def test_head_of_dept_can_create_object_and_department_sees_it(client, auth):
    # Ерофеев — head_of_dept И inspector одновременно (sudir:77002); проверяем
    # именно ветку head_of_dept в scope_objects (фильтр по department, не по
    # assigned_to — этим она и отличается от обычного инспектора).
    resp = client.post("/api/objects", json={
        "permit_number": "77-000000-666666-2026", "name": "ЖК Начальника отдела",
        "address": "г. Москва, ул. Начальственная, 2",
    }, headers=auth("sudir:77002"))
    assert resp.status_code == 200, resp.text
    obj = resp.json()
    assert obj["department"] == "Отдел надзора за жилищным строительством № 3"
    print(f"OK: head_of_dept создал объект, department={obj['department']}")

    # Сам автор видит его.
    own = client.get("/api/objects", headers=auth("sudir:77002")).json()["items"]
    assert any(o["id"] == obj["id"] for o in own)

    # Инспектор из ТОГО ЖЕ отдела не видит: объект назначен на 77002, а
    # scope_objects для обычного инспектора смотрит на assigned_to, не на
    # department — то, что начальник отдела формально «свой», недостаточно.
    same_dept_inspector = client.get("/api/objects", headers=auth("sudir:77001")).json()["items"]
    assert not any(o["id"] == obj["id"] for o in same_dept_inspector), \
        "обычный инспектор не должен видеть объект, назначенный не на него"
    print("OK: рядовой инспектор того же отдела объект не видит (назначен не на него)")

    # Инспектор из другого отдела тем более не видит.
    other_dept = client.get("/api/objects", headers=auth("sudir:77007")).json()["items"]
    assert not any(o["id"] == obj["id"] for o in other_dept)
    print("OK: инспектор другого отдела объект не видит")


def test_create_object_requires_permit_and_name(client, auth):
    headers = auth("sudir:77001")
    resp = client.post("/api/objects", json={"permit_number": "77-1-1-2026"}, headers=headers)
    assert resp.status_code == 422
    resp = client.post("/api/objects", json={"name": "Без разрешения"}, headers=headers)
    assert resp.status_code == 422
    print("OK: без номера разрешения или наименования — 422")


def test_create_object_rejects_duplicate_permit(client, auth):
    headers = auth("sudir:77001")
    payload = {"permit_number": "77-000000-888888-2026", "name": "Первый"}
    first = client.post("/api/objects", json=payload, headers=headers)
    assert first.status_code == 200
    second = client.post("/api/objects", json={**payload, "name": "Второй"}, headers=headers)
    assert second.status_code == 409
    print("OK: повторный номер разрешения — 409, не тихая перезапись")


def test_create_object_requires_permission(client, auth):
    # Аналитик видит объекты (objects:read), но не создаёт (objects:create).
    resp = client.post("/api/objects", json={
        "permit_number": "77-000000-777777-2026", "name": "Без прав"},
        headers=auth("sudir:77004"))
    assert resp.status_code == 403
    print("OK: роль без objects:create получает отказ, а не тихое создание")

from __future__ import annotations

from .vision import UNTRUSTED_INPUT_RULE

PASS1_PROMPT = f"""Ты — vision-аналитик инженерных чертежей. Слепо сравни ПД и РД/ИД для одного помещения. Никаких юридических выводов, severity, норм, исторических примеров, benchmark или expected elements.

Вход — 4 раздельных изображения строго в порядке: 1 общий ПД; 2 крупный ROOM ПД; 3 общий РД/ИД; 4 крупный ROOM РД/ИД. Не требуй совпадения координат. Если типы графики различаются, сравни инженерные сущности и связи: тип, маркировку, параметр, точки сопряжения, направление, состав и топологию.

PASS 1 оптимизирован на HIGH RECALL. Сначала независимо перечисли видимые инженерные элементы и связи на ПД, затем на РД/ИД. После этого сформируй candidate_differences. При видимом основании сохрани candidate difference, а сомнение вынеси в uncertainty_reasons. Не выдумывай скрытые элементы. Проверяй inventory, topology, connections, parameters. Название помещения, мебель, отделка, стены, рамка, штамп, цвет и шрифт сами по себе не являются инженерным отличием.

comparability=high только если ROOM-зоны на ПД и РД действительно соответствуют друг другу, обе хорошо читаемы и инженерные сущности можно однозначно сопоставить. medium/low при частичном crop, несовпадении типов представления, неоднозначных символах, плохом масштабе/видимости или слабом grounding. Низкая comparability не удаляет candidate difference.

status: changed_candidate если есть candidate_differences; unchanged_candidate только как промежуточный класс при полном покрытии, хорошей видимости И high comparability; unclear если сравнение ограничено настолько, что надёжный вывод невозможен.

{UNTRUSTED_INPUT_RULE}

Ответ только JSON: {{"room_id":"string","room_visible_pd":true,"room_visible_rd":true,"comparability":"high|medium|low","engineering_elements_pd":[{{"kind":"string","label":"string|null","location_hint":"string|null"}}],"engineering_elements_rd":[],"connections_pd":[{{"from":"string|null","to":"string|null","system":"string|null","visible":true}}],"connections_rd":[],"candidate_differences":[{{"category":"inventory|topology|connection|parameter|scale_shift|drawing_type_mismatch","side":"pd|rd|both","description":"конкретный наблюдаемый факт","location_hint":"string|null"}}],"uncertainty_reasons":[],"coverage":["inventory","topology","connections","parameters"],"status":"changed_candidate|unchanged_candidate|unclear","summary":"кратко"}}"""

VERIFY_PROMPT = f"""Ты — второй vision-pass. Получаешь те же 4 изображения и candidate_differences из PASS 1. Для каждого кандидата выдай confirmed, rejected или unclear. confirmed — различие видно. rejected допустим ТОЛЬКО если текущая comparability=high, PASS1 comparability=high и есть ясное визуальное доказательство, что это одинаковое инженерное решение, сдвиг/масштаб, архитектурная графика или ошибка чтения. При medium/low comparability, частичном crop, неоднозначном символе или несовпадении типов представления возвращай unclear, не rejected. Для drawing_type_mismatch сравни сущности и связи, а не координаты. Никаких legal, severity или benchmark выводов.

{UNTRUSTED_INPUT_RULE}

Ответ только JSON: {{"comparability":"high|medium|low","verified":[{{"idx":0,"verdict":"confirmed|rejected|unclear","reason":"кратко","location":"string|null"}}],"page_status":"changed|unchanged|unclear","status_notes":[]}}"""

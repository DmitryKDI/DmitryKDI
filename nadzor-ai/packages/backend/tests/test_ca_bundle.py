"""Сертификат кладут файлом в каталог — и всё (Г.110).

Вопрос пользователя: «как добавить SSL с ПК, может файл закинуть?». До
этого ответ был «пропишите путь в переменной окружения», а типовой совет из
интернета на CERTIFICATE_VERIFY_FAILED — отключить проверку, что снимает
защиту от подмены на всём канале и ничего не чинит.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llm  # noqa: E402

_FAKE_CERT = b"-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----\n"


def _certs_dir(*names: str) -> Path:
    directory = Path(tempfile.mkdtemp())
    for name in names:
        (directory / name).write_bytes(_FAKE_CERT)
    llm.CERTS_DIR = directory
    os.environ.pop("GIGACHAT_CA_BUNDLE", None)
    return directory


def test_certificate_dropped_into_the_folder_is_picked_up():
    """Достаточно положить файл: ни переменных, ни правки кода."""
    _certs_dir("root.pem")
    value = llm.ca_bundle()
    assert isinstance(value, str), value
    assert Path(value).exists()
    assert _FAKE_CERT in Path(value).read_bytes()
    print("OK: сертификат из каталога подхвачен без единой настройки")


def test_system_roots_are_kept_not_replaced():
    """Набор СКЛЕИВАЕТСЯ с системным: иначе, починив один хост, мы сломали
    бы проверку всех остальных."""
    _certs_dir("root.pem")
    bundle = Path(llm.ca_bundle()).read_bytes()
    import certifi
    system = Path(certifi.where()).read_bytes()
    assert system in bundle, "системные корни потеряны"
    assert _FAKE_CERT in bundle
    print("OK: системные корни сохранены, свой сертификат добавлен")


def test_several_certificates_all_get_in():
    _certs_dir("first.pem", "second.crt", "third.cer")
    bundle = Path(llm.ca_bundle()).read_text(encoding="utf-8", errors="replace")
    assert bundle.count("BEGIN CERTIFICATE") >= 3
    print("OK: в набор попадают все файлы каталога")


def test_bundle_is_rebuilt_when_a_certificate_changes():
    """Имя набора — отпечаток содержимого, поэтому замена файла подхватится,
    а лишней сборки при каждом вызове не будет."""
    directory = _certs_dir("root.pem")
    first = llm.ca_bundle()
    assert llm.ca_bundle() == first, "набор пересобирается на каждом вызове"
    (directory / "root.pem").write_bytes(_FAKE_CERT + b"changed\n")
    assert llm.ca_bundle() != first, "изменённый сертификат не подхватился"
    print("OK: изменение сертификата пересобирает набор, повтор — нет")


def test_environment_variable_still_wins():
    """Явно заданный путь побеждает каталог: у администратора должен
    оставаться способ указать свой набор."""
    _certs_dir("root.pem")
    os.environ["GIGACHAT_CA_BUNDLE"] = "/etc/ssl/custom.pem"
    try:
        assert llm.ca_bundle() == "/etc/ssl/custom.pem"
    finally:
        os.environ.pop("GIGACHAT_CA_BUNDLE", None)
    print("OK: переменная окружения побеждает каталог")


def test_disabled_verification_is_never_silent():
    """Отключение проверки — последнее средство, и оно обязано быть видно:
    «проверка отключена» не должно выглядеть так же, как «всё в порядке»."""
    _certs_dir()
    os.environ["GIGACHAT_CA_BUNDLE"] = "false"
    try:
        assert llm.ca_bundle() is False
        assert "ОТКЛЮЧЕНА" in llm.ca_bundle_description()
    finally:
        os.environ.pop("GIGACHAT_CA_BUNDLE", None)
    print("OK: отключённая проверка названа словами, а не спрятана")


def test_empty_folder_means_system_roots():
    _certs_dir()
    assert llm.ca_bundle() is True
    assert "системный" in llm.ca_bundle_description()
    print("OK: пустой каталог — обычная системная проверка")


def test_tls_failure_says_what_to_do_not_just_what_broke():
    """На ошибке сертификата пользователь должен прочитать действие, а не
    только диагноз — иначе он найдёт совет «отключить проверку»."""
    advice = llm._tls_advice(Exception("[SSL: CERTIFICATE_VERIFY_FAILED] self-signed"))
    assert "положите файл" in advice.lower()
    assert str(llm.CERTS_DIR) in advice
    assert llm._tls_advice(Exception("Connection reset by peer")) == ""
    print("OK: ошибка сертификата объясняет, что именно сделать")


if __name__ == "__main__":
    test_certificate_dropped_into_the_folder_is_picked_up()
    test_system_roots_are_kept_not_replaced()
    test_several_certificates_all_get_in()
    test_bundle_is_rebuilt_when_a_certificate_changes()
    test_environment_variable_still_wins()
    test_disabled_verification_is_never_silent()
    test_empty_folder_means_system_roots()
    test_tls_failure_says_what_to_do_not_just_what_broke()
    print("ALL PASS")

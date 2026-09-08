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

# Настоящий самоподписанный сертификат нужен там, где набор обязан быть
# принят OpenSSL, а не просто собран: подделка из строки этого не проверит.
_REAL_PEM = """-----BEGIN CERTIFICATE-----
MIIDEzCCAfugAwIBAgIUSfub3L21Nln/3m2P4g3MYm4xXUMwDQYJKoZIhvcNAQEL
BQAwGTEXMBUGA1UEAwwOVGVzdCBSb290IENBIDEwHhcNMjYwOTA4MTQyNDUyWhcN
MzYwOTA1MTQyNDUyWjAZMRcwFQYDVQQDDA5UZXN0IFJvb3QgQ0EgMTCCASIwDQYJ
KoZIhvcNAQEBBQADggEPADCCAQoCggEBAJdMGuwMtp06Zo5dIzpAB6iB3EfeNTbk
v+8zimMtysiXhJaAHC2xDYw17lWz8vzgZl3SHa9rsrgfBHW0ZACUVnxk67xHzu6d
8ZgdQdvyAXeSoKQznqvTCm6QLFYgutJKDHV1tixOjwkMQbixCij5ws9jKvRBYgD5
3DUUhYLzadA2JxBOXSrczshtEI/irL9mq3k27y9jvc0n0suYqeekFeBdCa9stfV+
5ntR7tYrCrrVJOD8cPh04UIA2x1LncUkq+AgfbTQEWk5k11LPyl9TwMaDCNse2eJ
bUTVKHO2eL2kFiPL/EtXjLkD4LkrZXMd7E86w8cu/TNfC5Q6s+pv15sCAwEAAaNT
MFEwHQYDVR0OBBYEFLMjVFAhgkbVIeiq2krxFzx3L6X6MB8GA1UdIwQYMBaAFLMj
VFAhgkbVIeiq2krxFzx3L6X6MA8GA1UdEwEB/wQFMAMBAf8wDQYJKoZIhvcNAQEL
BQADggEBAA5plbie+35CYm6umFW+18FBhJ76k8Air751mQenQ4ktUujmp0IR/7Ef
GIeBglWXKTG3yCmVzP6MWm8oiPGOIAuDUqIszyTtiTcQvYxA6tAwuBeya8S+WyvE
rqEwKnZXwqNGfSk7O6H7738F079aAsNgai0jO8NkxQrBe3ZAkEwB8RmUVoR4848m
hfwIy/KqqsNjVYUF+i/Rz+3xpMD+erKihjka5OowLvyV0mIej2ETtmZNbpmAuSfZ
/xfLeQTzPBssUh6sqY3NAui4XMXvRgek1VhpMYXt1iL4V7xFaP98YO39uP2x1qox
cBKWB7ykmRrBPYAAizcn1JSwKJlsYmM=
-----END CERTIFICATE-----
"""
_REAL_PEM_2 = """-----BEGIN CERTIFICATE-----
MIIDEzCCAfugAwIBAgIUf3yvuKQbKY5u1CAI5B/F7nnW7SAwDQYJKoZIhvcNAQEL
BQAwGTEXMBUGA1UEAwwOVGVzdCBSb290IENBIDIwHhcNMjYwOTA4MTQyNDUyWhcN
MzYwOTA1MTQyNDUyWjAZMRcwFQYDVQQDDA5UZXN0IFJvb3QgQ0EgMjCCASIwDQYJ
KoZIhvcNAQEBBQADggEPADCCAQoCggEBAMt5VoiwgAA1Z06rZAX40eJqpVd4mRNM
blF08qW8mKvuLFFHhtI5gvo8kaMV5RmPqU7QPs85EM7OZV4UJok3mew72hWvEzif
YtER2hSfvARJdbPmNsPwxKBn1qiVgdXOuhmupaeIJZjrlWG8zS/QLMRM7QYse0Lv
D/8xbzqL0OBcC95IdreKxQRQWUZyBdrZi9BWYvxQvjHLCH4qMCiDOtKDqb433z59
2DuNPL3glF0nW/4l0BFKBinXrc9fuCEuj6JcE4OblArajrsmlz+rTIPeYSEsdgOz
qjZPdOJc3X863HN5FasUeFUvRBcTfEe//I6aGDssNOrQ12BTCPWASe0CAwEAAaNT
MFEwHQYDVR0OBBYEFEygxnDRYK7rGjRb7LeOskDCusUwMB8GA1UdIwQYMBaAFEyg
xnDRYK7rGjRb7LeOskDCusUwMA8GA1UdEwEB/wQFMAMBAf8wDQYJKoZIhvcNAQEL
BQADggEBAGyQyiDhTTEGNwi56StmJVHd5XPtpb4NzV0zpGhK4jKNNPihCyydakri
wsp1W9KVUup2PWv0FEHabZ0YsH9l7QbErzI2+SYHV335P5YwUI8quGGzkNGYwLpy
ZCr4U+3Dnafeuhfuak3hztmA2gHkm2MKViMwb04KfHRjcokxDAqgLsp6gE3Lt8h3
5ceVO/gUjd0Hvxxk/gbxPQxRcwVE/exoOdBAvv2CGLR0F8NvOt1F1FBMkYEY3Pjh
FQhR87TBAwzgLnAXpSumBq50V4+sIeFl9W4aymHLHbMEOUkAfAOS774OZb1IHSMh
s/IeTjPj4wto0DZFET5EM3FXJJ53Q1Y=
-----END CERTIFICATE-----
"""



def _certs_dir(*names: str) -> Path:
    directory = Path(tempfile.mkdtemp())
    for name in names:
        (directory / name).write_bytes(_REAL_PEM.encode('ascii'))
    llm.CERTS_DIR = directory
    os.environ.pop("GIGACHAT_CA_BUNDLE", None)
    return directory


def test_certificate_dropped_into_the_folder_is_picked_up():
    """Достаточно положить файл: ни переменных, ни правки кода."""
    _certs_dir("root.pem")
    value = llm.ca_bundle()
    assert isinstance(value, str), value
    assert Path(value).exists()
    assert _REAL_PEM.encode('ascii') in Path(value).read_bytes()
    print("OK: сертификат из каталога подхвачен без единой настройки")


def test_system_roots_are_kept_not_replaced():
    """Набор СКЛЕИВАЕТСЯ с системным: иначе, починив один хост, мы сломали
    бы проверку всех остальных."""
    _certs_dir("root.pem")
    bundle = Path(llm.ca_bundle()).read_bytes()
    import certifi
    system = Path(certifi.where()).read_bytes()
    assert system in bundle, "системные корни потеряны"
    assert _REAL_PEM.encode('ascii') in bundle
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
    (directory / "root.pem").write_bytes(_REAL_PEM_2.encode("ascii"))
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


def test_binary_certificate_is_converted_not_glued_as_is():
    """Официальная выгрузка для Windows — файлы .cer в двоичном виде (DER).
    Склеенные как есть, они дают набор, который OpenSSL не читает, и
    проверка молча остаётся сломанной. Формат определяется по содержимому,
    а не по расширению."""
    import ssl as _ssl
    directory = Path(tempfile.mkdtemp())
    der = _ssl.PEM_cert_to_DER_cert(_REAL_PEM)
    (directory / "root.cer").write_bytes(der)
    llm.CERTS_DIR = directory
    os.environ.pop("GIGACHAT_CA_BUNDLE", None)

    bundle = llm.ca_bundle()
    assert isinstance(bundle, str)
    context = _ssl.create_default_context(cafile=bundle)
    assert len(context.get_ca_certs()) >= 1, "OpenSSL не принял собранный набор"
    print("OK: двоичный сертификат преобразован в текстовый и принят OpenSSL")


def test_archive_is_read_without_unpacking_by_hand():
    """С сайта удостоверяющего центра сертификаты отдают архивом. Требовать
    от пользователя распаковку — лишний шаг, на котором он ошибётся."""
    import ssl as _ssl
    import zipfile
    directory = Path(tempfile.mkdtemp())
    with zipfile.ZipFile(directory / "roots.zip", "w") as archive:
        archive.writestr("Root_CA.cer", _ssl.PEM_cert_to_DER_cert(_REAL_PEM))
        archive.writestr("readme.txt", "не сертификат, должен быть пропущен")
    llm.CERTS_DIR = directory
    os.environ.pop("GIGACHAT_CA_BUNDLE", None)

    context = _ssl.create_default_context(cafile=llm.ca_bundle())
    assert len(context.get_ca_certs()) >= 1
    print("OK: сертификат прочитан прямо из архива, посторонний файл пропущен")


def test_broken_file_is_skipped_with_a_reason_not_silently():
    """Негодный файл не должен ронять запуск и не должен исчезать молча."""
    directory = Path(tempfile.mkdtemp())
    (directory / "мусор.cer").write_bytes(b"\x00\x01 not a certificate")
    (directory / "good.pem").write_bytes(_REAL_PEM.encode("ascii"))
    llm.CERTS_DIR = directory
    os.environ.pop("GIGACHAT_CA_BUNDLE", None)

    import ssl as _ssl
    context = _ssl.create_default_context(cafile=llm.ca_bundle())
    assert len(context.get_ca_certs()) >= 1, "годный сертификат потерян из-за негодного"
    print("OK: негодный файл пропущен, годный сохранён")


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
    test_certificates_in_a_subfolder_are_found_too()
    print("ALL PASS")


def test_certificates_in_a_subfolder_are_found_too():
    """Распаковка архива в проводнике Windows создаёт подпапку с именем
    архива. Первая версия искала только в самом каталоге и молча оставалась
    на системном наборе: пользователь сделал всё правильно и не получил ни
    результата, ни сообщения. Найдено проверкой реального сценария, а не
    рассуждением.
    """
    import ssl as _ssl
    directory = Path(tempfile.mkdtemp())
    nested = directory / "windows_russian_trusted_root_ca"
    nested.mkdir()
    (nested / "root.cer").write_bytes(_ssl.PEM_cert_to_DER_cert(_REAL_PEM))
    llm.CERTS_DIR = directory
    os.environ.pop("GIGACHAT_CA_BUNDLE", None)

    bundle = llm.ca_bundle()
    assert isinstance(bundle, str), "сертификат из подпапки не найден"
    assert len(_ssl.create_default_context(cafile=bundle).get_ca_certs()) >= 1
    print("OK: сертификаты во вложенной папке найдены")

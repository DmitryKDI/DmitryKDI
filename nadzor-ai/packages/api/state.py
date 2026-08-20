"""Общие объекты приложения: конфигурация, провайдеры, нормативная база, аудит."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from audit.chain import AuditChain
from integrations.identity.mock import MockIdentityProvider
from integrations.identity.sudir import SudirIdentityProvider
from integrations.urban_data.iais_ogd import IaisOgdProvider
from integrations.urban_data.mock import MockUrbanDataProvider
from llm_core.router import ProviderRouter
from norms import load_norms

ROOT = Path(os.environ.get("APP_ROOT", Path(__file__).resolve().parents[2]))
MANIFEST = ROOT / "data/demo/generated/manifest.json"
STORAGE_ROOT = Path(os.environ.get("STORAGE_ROOT", ROOT / "data/storage"))


def _yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))


class AppState:
    """Единая точка доступа к настройкам и внешним адаптерам."""

    def __init__(self) -> None:
        self.root = ROOT
        self.storage_root = STORAGE_ROOT
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.providers_config = _yaml("providers.yaml")
        self.rules_config = _yaml("rules.yaml")
        self.scoring_config = _yaml("scoring.yaml")
        self.limits_config = _yaml("limits.yaml")
        self.region_config = _yaml("regions/moscow.yaml")
        self.router = ProviderRouter(self.providers_config)
        self.norms = load_norms(ROOT / "data/norms/norms.json",
                                ROOT / "data/norms/classifier.json")
        self.identity = self._identity()
        self.urban_data = self._urban_data()
        self.audit = AuditChain()

    def _identity(self):
        if os.environ.get("IDENTITY_PROVIDER", "mock") == "sudir":
            return SudirIdentityProvider()
        return MockIdentityProvider.from_manifest(MANIFEST)

    def _urban_data(self):
        if os.environ.get("URBAN_DATA_PROVIDER", "mock") == "iais_ogd":
            return IaisOgdProvider()
        return MockUrbanDataProvider.from_manifest(MANIFEST)

    def reload_configs(self) -> None:
        """Перечитать конфигурацию без пересборки: смена провайдера и правил."""
        self.providers_config = _yaml("providers.yaml")
        self.rules_config = _yaml("rules.yaml")
        self.scoring_config = _yaml("scoring.yaml")
        self.router = ProviderRouter(self.providers_config)

    def save_providers_config(self) -> None:
        (self.root / "config/providers.yaml").write_text(
            yaml.safe_dump(self.providers_config, allow_unicode=True, sort_keys=False),
            encoding="utf-8")

    def save_rules_config(self) -> None:
        (self.root / "config/rules.yaml").write_text(
            yaml.safe_dump(self.rules_config, allow_unicode=True, sort_keys=False),
            encoding="utf-8")


state = AppState()

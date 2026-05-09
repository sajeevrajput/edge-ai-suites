"""Configuration management :YAML + Pydantic Settings with env var resolution."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings


_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)(?::-(.*?))?\}")


def _resolve_env_vars(value: str) -> str:
    def _replacer(match: re.Match) -> str:
        var_name = match.group(1)
        default = match.group(2)
        env_val = os.environ.get(var_name)
        if env_val is None:
            if default is not None:
                return default
            print(
                f"FATAL: environment variable '{var_name}' is not set "
                f"but is referenced in config.yaml",
                file=sys.stderr,
            )
            sys.exit(1)
        return env_val
    return _ENV_VAR_PATTERN.sub(_replacer, value)


def _resolve_recursive(obj):
    if isinstance(obj, str):
        return _resolve_env_vars(obj)
    if isinstance(obj, dict):
        return {k: _resolve_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_recursive(item) for item in obj]
    return obj


class NvrAuthConfig(BaseModel):
    """NVR auth credentials.

    Per the ADD chat (comment 26), the plugin **facilitates** auth — it
    does not maintain long-lived sessions. Credentials here are used only
    when we have to call the VMS during discover/register/write-back.
    Per-request token pass-through from the App is left as a v2 hook.
    """
    username: str = ""
    password: str = ""
    auth_type: Literal["basic", "digest", "none"] = "none"


class NvrInstanceConfig(BaseModel):
    name: str
    vendor: Literal["frigate", "nx_witness"]
    base_url: str = ""
    auth: NvrAuthConfig = Field(default_factory=NvrAuthConfig)
    # Path to a JSON file containing Nx analytics integration manifests.
    # Expected keys: integrationManifest, engineManifest, deviceAgentManifest, pinCode.
    # Used only by the nx_witness vendor.
    analytics_manifest_path: str | None = None


class LiveCaptioningCoreAppConfig(BaseModel):
    type: Literal["live_captioning"] = "live_captioning"
    base_url: str
    mediamtx_url: str = ""
    default_model: str = ""
    default_prompt: str = "Describe what is happening in this video."
    max_tokens: int = 100
    delivery_timeout_seconds: int = 30
    default_pipeline: str = "GenAI_Pipeline_on_CPU"


class ObjectDetectionCoreAppConfig(BaseModel):
    """Config for DLStreamer Pipeline Server–based object detection apps (e.g. PDD)."""
    type: Literal["object_detection"] = "object_detection"
    # Identifies this app instance in API URLs (e.g. "pdd" → /v1/core-apps/pdd/runs)
    app_id: str = "pdd"
    display_name: str = "Object Detection"
    base_url: str  # Pipeline Server REST URL
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    # Broker address as seen by the Pipeline Server (used in the destination payload
    # so gvametapublish can connect). Defaults to the Pipeline Server's MQTT_HOST env var
    # value (container name on the PDD network). Set to "host.docker.internal" if the
    # broker is only reachable via the host's published port.
    pipeline_server_mqtt_host: str = "mqtt-broker"
    pipeline_server_mqtt_port: int = 1883


AnyCorAppConfig = LiveCaptioningCoreAppConfig | ObjectDetectionCoreAppConfig

# Discriminated union for Pydantic to pick the right config model from the YAML `type` field.
_DiscriminatedCoreAppConfig = Annotated[
    LiveCaptioningCoreAppConfig | ObjectDetectionCoreAppConfig,
    Field(discriminator="type"),
]


class MqttConfig(BaseModel):
    host: str = ""
    port: int = 1883


class ApiConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    api_key: str = ""


class DatabaseConfig(BaseModel):
    url: str = "postgresql+asyncpg://vms:vms@localhost:5432/vms_plugin"


class AppConfig(BaseModel):
    nvr_instances: list[NvrInstanceConfig] = Field(default_factory=list)
    core_apps: list[_DiscriminatedCoreAppConfig] = Field(default_factory=list)  # type: ignore[valid-type]
    api: ApiConfig = Field(default_factory=ApiConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    mqtt: MqttConfig = Field(default_factory=MqttConfig)

    @model_validator(mode="before")
    @classmethod
    def resolve_env_vars(cls, values):
        values = _resolve_recursive(values)
        if isinstance(values, dict) and "core_app" in values and "core_apps" not in values:
            legacy = values.pop("core_app")
            values["core_apps"] = [legacy] if legacy else []
        return values

    @property
    def core_app(self) -> "AnyCorAppConfig | None":
        return self.core_apps[0] if self.core_apps else None


class Settings(BaseSettings):
    config_path: str = "/app/config/config.yaml"
    model_config = {"env_prefix": "VMS_PLUGIN_"}


def load_config(path: str | Path | None = None) -> AppConfig:
    try:
        from dotenv import load_dotenv
        _env_file = Path(".env")
        if _env_file.exists():
            load_dotenv(dotenv_path=_env_file, override=False)
    except ImportError:
        pass

    settings = Settings()
    config_path = Path(path) if path else Path(settings.config_path)
    if not config_path.exists():
        print(f"FATAL: config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    with open(config_path) as f:
        raw = yaml.safe_load(f)
    return AppConfig(**raw)

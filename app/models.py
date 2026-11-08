from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


def valid_http_url(value: str) -> str:
    if not value:
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Use an http(s) URL without embedded credentials or fragment')
    return value.rstrip('/')


class DiscoveryInput(BaseModel):
    name: str = Field(default='', max_length=100)
    moonraker_url: str = Field(min_length=1)
    api_key: str = Field(default='', max_length=512)

    @field_validator('moonraker_url')
    @classmethod
    def url(cls, value):
        return valid_http_url(value)


class PrinterInput(BaseModel):
    model_config = ConfigDict(extra='ignore', allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=100)
    kind: Literal['fdm', 'belt', 'resin'] = 'fdm'
    profile: str = Field(min_length=1, max_length=100)
    volume: tuple[float, float, float]
    nozzle: float = Field(gt=0, le=2)
    material: str = Field(min_length=1, max_length=40)
    color: str = Field(default='', max_length=40)
    spool_id: int | None = Field(default=None, gt=0)
    moonraker_url: str = ''
    camera_url: str = ''
    api_key: str = Field(default='', max_length=512)
    enabled: bool = False

    @field_validator('moonraker_url', 'camera_url')
    @classmethod
    def urls(cls, value):
        return valid_http_url(value)

    @field_validator('volume')
    @classmethod
    def positive_volume(cls, value):
        if any(not 0 < v <= 10000 for v in value):
            raise ValueError('Each build dimension must be between 0 and 10000 mm')
        return value


class BatchInput(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, validate_default=True)
    name: str = Field(min_length=1, max_length=120)
    file_id: str
    quantity: int = Field(ge=1, le=100)
    profile: str = Field(min_length=1, max_length=100)
    volume: tuple[float, float, float]
    nozzle: float = Field(gt=0, le=2)
    material: str = Field(min_length=1, max_length=40)
    color: str = Field(default='', max_length=40)
    grams: float = Field(gt=0, le=100000)
    duration_minutes: float = Field(gt=0, le=100000)
    priority: int = Field(default=50, ge=0, le=100)
    not_before: str = ''

    @field_validator('volume')
    @classmethod
    def positive_volume(cls, value):
        return PrinterInput.positive_volume(value)

    @field_validator('not_before')
    @classmethod
    def timestamp(cls, value):
        if not value:
            return datetime.now(timezone.utc).isoformat()
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('Scheduled time must include timezone')
        return parsed.astimezone(timezone.utc).isoformat()


class SchedulerInput(BaseModel):
    enabled: bool


class ControlInput(BaseModel):
    action: Literal['pause', 'resume', 'cancel']


class ResolveInput(BaseModel):
    outcome: Literal['failed', 'completed', 'cancelled']

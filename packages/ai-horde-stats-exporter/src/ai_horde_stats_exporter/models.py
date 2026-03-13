"""Pydantic models for AI Horde API responses."""

from typing import Any

from pydantic import BaseModel, model_validator


class ApiModel(BaseModel):
    """Base for API response models. Strips null values so field defaults apply."""

    @model_validator(mode="before")
    @classmethod
    def _strip_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None}
        return data


class HordeModelStatus(ApiModel):
    name: str
    count: int = 0
    queued: float = 0
    performance: float = 0
    jobs: float = 0
    eta: int = 0


class WorkerKudosDetails(ApiModel):
    generated: float = 0
    uptime: float = 0


class HordeWorker(ApiModel):
    name: str
    online: bool = False
    requests_fulfilled: int = 0
    kudos_rewards: float = 0
    kudos_details: WorkerKudosDetails = WorkerKudosDetails()
    performance: str = "0"
    threads: int = 0
    models: list[str] = []
    uncompleted_jobs: int = 0
    uptime: int = 0
    maintenance_mode: bool = False
    trusted: bool = False
    flagged: bool = False
    nsfw: bool = False
    bridge_agent: str = "unknown"
    # image-specific
    max_pixels: int = 0
    megapixelsteps_generated: float = 0
    img2img: bool = False
    painting: bool = False
    lora: bool = False
    # text-specific
    max_length: int = 0
    max_context_length: int = 0
    tokens_generated: float = 0

    @property
    def parsed_performance(self) -> float:
        if isinstance(self.performance, (int, float)):
            return float(self.performance)
        try:
            return float(self.performance.split()[0])
        except (ValueError, IndexError):
            return 0.0

    @property
    def model_count(self) -> int:
        return len(self.models)


class PerformanceStatus(ApiModel):
    queued_requests: int = 0
    queued_text_requests: int = 0
    worker_count: int = 0
    text_worker_count: int = 0
    interrogator_count: int = 0
    thread_count: int = 0
    text_thread_count: int = 0
    interrogator_thread_count: int = 0
    past_minute_megapixelsteps: float = 0
    past_minute_tokens: float = 0
    queued_megapixelsteps: float = 0
    queued_tokens: float = 0
    queued_forms: int = 0


class ImageStatsPeriod(ApiModel):
    images: int = 0
    ps: int = 0


class ImageStatsResponse(ApiModel):
    minute: ImageStatsPeriod = ImageStatsPeriod()
    hour: ImageStatsPeriod = ImageStatsPeriod()
    day: ImageStatsPeriod = ImageStatsPeriod()
    month: ImageStatsPeriod = ImageStatsPeriod()
    total: ImageStatsPeriod = ImageStatsPeriod()


class TextStatsPeriod(ApiModel):
    requests: int = 0
    tokens: int = 0


class TextStatsResponse(ApiModel):
    minute: TextStatsPeriod = TextStatsPeriod()
    hour: TextStatsPeriod = TextStatsPeriod()
    day: TextStatsPeriod = TextStatsPeriod()
    month: TextStatsPeriod = TextStatsPeriod()
    total: TextStatsPeriod = TextStatsPeriod()


class StatsModelsResponse(ApiModel):
    day: dict[str, int] = {}
    month: dict[str, int] = {}
    total: dict[str, int] = {}


class ModesResponse(ApiModel):
    maintenance_mode: bool = False
    invite_only_mode: bool = False
    raid_mode: bool = False


class HordeTeam(ApiModel):
    name: str = "unknown"
    requests_fulfilled: int = 0
    kudos: float = 0
    worker_count: int = 0

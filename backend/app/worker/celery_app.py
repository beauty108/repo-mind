"""
Celery application configuration.
Broker and result backend: Redis.
"""

from celery import Celery

from app.config import get_settings


def create_celery_app() -> Celery:
    settings = get_settings()

    app = Celery(
        "repomind",
        broker=settings.redis_url,
        backend=settings.redis_url,
        include=["app.worker.tasks"],
    )

    app.conf.update(
        # Serialization
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # Timezone
        timezone="UTC",
        enable_utc=True,
        # Task behavior
        task_acks_late=True,           # Acknowledge after task completes (safer)
        worker_prefetch_multiplier=1,  # Don't prefetch more tasks than concurrency
        task_reject_on_worker_lost=True,
        # Result expiry (24h)
        result_expires=86400,
        # Retry behavior for transient errors
        task_max_retries=3,
        # Soft time limit: log a warning; hard limit: kill the task
        task_soft_time_limit=600,      # 10 minutes soft
        task_time_limit=720,           # 12 minutes hard
    )

    return app


celery_app = create_celery_app()

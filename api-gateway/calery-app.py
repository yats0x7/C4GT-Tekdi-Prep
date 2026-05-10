# api-gateway/celery_app.py
# Celery configuration for async LLM task processing

from celery import Celery
import os

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery = Celery(
    "shiksha",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "tasks.ingestion",
        "tasks.assessment",
        "tasks.multimedia",
        "tasks.packaging",
    ]
)

celery.conf.update(
    # Task routing — keep LLM tasks on dedicated queues
    task_routes={
        "tasks.ingestion.*": {"queue": "ingestion"},
        "tasks.assessment.*": {"queue": "assessment"},
        "tasks.multimedia.*": {"queue": "multimedia"},
        "tasks.packaging.*": {"queue": "packaging"},
    },

    # Prevent memory exhaustion from long-running LLM tasks
    worker_max_tasks_per_child=10,
    worker_prefetch_multiplier=1,     # One task at a time per worker
    task_acks_late=True,              # Ack only after completion (safe retries)

    # Time limits (Llama 3 8B can be slow on CPU)
    task_soft_time_limit=300,         # 5 min soft limit → raises exception
    task_time_limit=600,              # 10 min hard limit → kills task

    # Retry policy for LLM failures
    task_max_retries=3,
    task_default_retry_delay=30,

    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    result_expires=3600,              # Keep results for 1 hour
    timezone="UTC",
    enable_utc=True,
)
"""KEBAB settings, loaded from environment and `.env`.

Pattern adapted from `better-ed-ai/app/config/config.py`:
- `Settings(BaseSettings)` with `Field(default=...)` for mandatory env vars.
- `@lru_cache get_settings()` for cheap singleton access.
- Module-level `env = get_settings()` for import-time binding.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv(".env.local", override=True)
load_dotenv(".env")


class Settings(BaseSettings):
    """Environment settings for KEBAB.

    All fields are prefixed with ``KEBAB_`` in the environment. Mandatory
    fields should be declared with ``Field(default=...)`` to fail fast at
    import time when misconfigured.
    """

    # Storage
    KNOWLEDGE_DIR: Path = Field(
        default=Path("./knowledge"),
        description="Root directory for curated markdown content.",
    )
    RAW_DIR: Path = Field(
        default=Path("./knowledge/raw"),
        description="Directory for untouched source binaries (pdf/html/csv/json). "
        "Never contains derived text — see PROCESSED_DIR.",
    )
    PROCESSED_DIR: Path = Field(
        default=Path("./knowledge/processed"),
        description="Directory for synthesized derivatives (extracted text, "
        "figure descriptions, image files). One folder per source document.",
    )
    CURATED_DIR: Path = Field(
        default=Path("./knowledge/curated"),
        description="Directory for curated markdown articles — the actual "
        "knowledge base (medallion-architecture 'gold' tier). Every domain/"
        "subdomain/topic/article lives under this root.",
    )
    QDRANT_PATH: str | None = Field(
        default="./knowledge/.qdrant",
        description="Local Qdrant storage path. Mutually exclusive with QDRANT_URL.",
    )
    QDRANT_URL: str | None = Field(
        default=None,
        description="Qdrant server URL. Takes precedence over QDRANT_PATH when set.",
    )
    QDRANT_COLLECTION: str = Field(
        default="knowledge",
        description="Qdrant collection name.",
    )

    # Bot identity
    BOT_CONTACT_EMAIL: str = Field(
        default="kebab@kebab.local",
        description="Contact email for User-Agent headers (required by Wikimedia API policy).",
    )

    # LLM credentials & defaults
    GOOGLE_API_KEY: str = Field(
        default="",
        description="Google AI Studio API key. Required for any Gemini call; "
        "empty value is tolerated at import time so the CLI can show --help.",
    )
    TAVILY_API_KEY: str = Field(
        default="",
        description="Tavily search API key. Required for the tavily adapter.",
    )

    # Azure OpenAI
    AZURE_OPENAI_ENDPOINT: str = Field(
        default="",
        description="Azure OpenAI endpoint URL (e.g. https://my-resource.openai.azure.com/).",
    )
    AZURE_OPENAI_API_KEY: str = Field(
        default="",
        description="Azure OpenAI API key.",
    )

    # AWS Bedrock
    AWS_ACCESS_KEY_ID: str = Field(
        default="",
        description="AWS access key for Bedrock.",
    )
    AWS_SECRET_ACCESS_KEY: str = Field(
        default="",
        description="AWS secret key for Bedrock.",
    )
    AWS_REGION: str = Field(
        default="us-east-1",
        description="AWS region for Bedrock.",
    )

    # MiniMax
    MINIMAX_BASE_URL: str = Field(
        default="",
        description="MiniMax API base URL.",
    )
    MINIMAX_API_KEY: str = Field(
        default="",
        description="MiniMax API key.",
    )

    GEMINI_MODEL: str = Field(
        default="google-gla:gemini-2.5-flash",
        description="Default Gemini model identifier (pydantic-ai prefix).",
    )
    FAST_MODEL: str = Field(
        default="google-gla:gemini-2.5-flash-lite",
        description="Cheaper / faster model for high-volume calls.",
    )
    EVAL_MODEL: str = Field(
        default="google-gla:gemini-2.5-flash",
        description="Judge model used by eval suites. Distinct from "
        "GEMINI_MODEL to avoid same-model self-enhancement bias.",
    )
    LLM_MAX_RETRIES: int = Field(
        default=5,
        description="Default `retries=` for every pydantic-ai Agent.",
    )
    LLM_CURATION_MODEL: str = Field(
        default="gemini-flash",
        description="Legacy default. Per-operation settings below are preferred.",
    )

    # Per-operation model settings. All default to gemini-flash.
    # Set to any alias from models.yaml or a provider:model string.
    ORGANIZE_MODEL: str = Field(
        default="gemini-flash",
        description="Model for hierarchy proposal (organize stage).",
    )
    GENERATE_MODEL: str = Field(
        default="gemini-flash",
        description="Model for article generation (generate stage).",
    )
    CONTEXTS_MODEL: str = Field(
        default="gemini-flash",
        description="Model for context classification (contexts stage).",
    )
    RESEARCH_PLANNER_MODEL: str = Field(
        default="gemini-flash",
        description="Model for research claim extraction and query planning.",
    )
    RESEARCH_EXECUTOR_MODEL: str = Field(
        default="gemini-flash",
        description="Model for classifying research findings.",
    )
    RESEARCH_JUDGE_MODEL: str = Field(
        default="gemini-flash",
        description="Model for judging whether disputes are genuine.",
    )
    QA_MODEL: str = Field(
        default="gemini-flash",
        description="Model for Q&A pair generation (qa agent).",
    )
    LINT_MODEL: str = Field(
        default="gemini-flash",
        description="Model for lint health checks (lint agent).",
    )
    FIGURE_MODEL: str = Field(
        default="gemini-flash",
        description="Model for describing PDF figures (ingest stage).",
    )

    LLM_VERIFICATION_MODELS: list[str] = Field(
        default_factory=lambda: ["$GEMINI_MODEL", "$FAST_MODEL"],
        description="Legacy: models for multi-LLM verification (replaced by research agent).",
    )
    EMBEDDING_MODEL: str = Field(
        default="gemini-embedding-001",
        description="Embedding model used by `kebab sync`.",
    )
    EMBEDDING_DIM: int = Field(
        default=768,
        description="Target embedding dimensionality. `gemini-embedding-001` "
        "returns 3072 by default but supports Matryoshka reduction down to 768.",
    )

    # Figure filters — applied before any multimodal LLM call during PDF ingest.
    # Defaults chosen from real DepEd/OpenStax-like corpus data (see
    # analyze_figures_relative.py). Tuning these lowers the rate at which
    # `describe_image` is called without losing pedagogical content.
    FIGURE_MIN_REL_AREA: float = Field(
        default=0.015,
        description="Drop figures smaller than this fraction of the page "
        "area (default 1.5%). Catches small activity icons (~220x220), "
        "bullet markers, section icons, and page seals. The repeated-hash "
        "rule catches duplicates regardless of size.",
    )
    FIGURE_REPEAT_PAGE_THRESHOLD: int = Field(
        default=3,
        description="Drop figures whose SHA256 content hash appears on this "
        "many distinct pages of the same document. Catches page headers, "
        "watermarks, section dividers regardless of size.",
    )
    FIGURE_RIBBON_ASPECT: float = Field(
        default=10.0,
        description="Aspect ratio (width/height) above which a small figure "
        "is treated as a decorative ribbon/separator bar.",
    )
    FIGURE_RIBBON_MAX_REL_AREA: float = Field(
        default=0.05,
        description="Ribbon filter only applies when the figure is smaller "
        "than this fraction of the page (default 5%). Wide content figures "
        "like timelines or number lines tend to be larger and stay kept.",
    )
    FIGURE_SOLID_COLOR_THRESHOLD: float = Field(
        default=0.99,
        description="Drop figures where this fraction or more of the pixels "
        "belong to the single most-common color (default 99%). Strict "
        "threshold — only fires on near-perfectly-uniform rectangles "
        "(solid black, solid white, flat color blocks). Borderline cases "
        "(0.95–0.99 usage) are left to the tiny/repeated rules or kept.",
    )

    # Source path metadata extraction
    SOURCE_PATH_PATTERN: str | None = Field(
        default=None,
        description="Pattern to extract metadata from raw source paths. "
        "Use {field_name} placeholders, e.g. "
        "'raw/documents/grade_{grade}/{subject}/{filename}'. "
        "Extracted fields are stored in the source index metadata.",
    )

    # Limits
    MAX_TOKENS_PER_ARTICLE: int = Field(
        default=50_000,
        description="Hard ceiling for a single article's markdown body.",
    )

    # M17 source gathering
    ALLOWED_SOURCE_DOMAINS: list[str] = Field(
        default_factory=list,
        description="Allowlist of hostnames that outbound fetchers may contact. "
        "Empty list = allow all (dev default). In production, restrict to the "
        "specific providers your adapters use. Hostnames match exactly OR as a "
        "suffix after a dot (e.g. 'wikipedia.org' matches 'en.wikipedia.org').",
    )
    GATHER_BUDGET_USD_PER_DAY: float = Field(
        default=1.00,
        description="Hard daily spend cap for automated source gathering "
        "(research agent, Tavily search). Enforced by M21's research agent.",
    )
    GATHER_CACHE_DIR: Path = Field(
        default=Path("./knowledge/.kebab/cache"),
        description="Directory for adapter HTTP response caches, candidates "
        "files, and inbox state. Gitignored.",
    )

    # Logging
    LOGS_DIR: str = Field(
        default="./logs",
        description="Base directory for log files.",
    )
    LOGFIRE_TOKEN: str | None = Field(
        default=None,
        description="Logfire write token. When unset, logfire runs in local-only mode.",
    )
    LOGFIRE_ENVIRONMENT: str = Field(
        default="local",
        description="Logfire environment tag (local, dev, prod).",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        env_ignore_empty=True,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()


env = get_settings()

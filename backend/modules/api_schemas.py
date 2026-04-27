"""
╔══════════════════════════════════════════════════════════════════╗
║  MODULE — API SCHEMAS (Pydantic response models)                 ║
║  Typage strict des réponses JSON pour OpenAPI + génération de    ║
║  types frontend via `openapi-typescript`.                        ║
║                                                                  ║
║  Conçu additif : on annote `response_model=` sur les endpoints   ║
║  existants sans toucher à leur logique métier.                   ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    """Base config : ignore les clés non déclarées, pas strict (rétrocompat)."""
    model_config = ConfigDict(extra="allow", populate_by_name=True)


# ─────────────────────────────────────────────────────────────────
# /api/status
# ─────────────────────────────────────────────────────────────────

class StatusResponse(_Base):
    ok: bool
    timestamp: str
    trading_blocked: bool
    nuclear_stop: bool
    open_positions: int
    max_positions: int
    daily_drawdown_pct: float
    account_equity: float
    regime: str
    vix: float | None = None
    broker_mode: str


# ─────────────────────────────────────────────────────────────────
# /api/portfolio
# ─────────────────────────────────────────────────────────────────

class EquityState(_Base):
    starting_equity: float
    current_equity: float
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    date: str | None = None
    last_update: str | None = None
    open_positions: list[dict[str, Any]] = Field(default_factory=list)


class PortfolioStats(_Base):
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    realized_pnl: float
    unrealized_pnl: float
    account_equity: float


class PortfolioResponse(_Base):
    equity: EquityState
    journal: list[dict[str, Any]] = Field(default_factory=list)
    open_positions: list[dict[str, Any]] = Field(default_factory=list)
    closed_trades: list[dict[str, Any]] = Field(default_factory=list)
    stats: PortfolioStats


# ─────────────────────────────────────────────────────────────────
# /api/macro
# ─────────────────────────────────────────────────────────────────

class MacroResponse(_Base):
    regime: str
    vix: float | None = None
    sp500: float | None = None
    ema200: float | None = None
    sp500_vs_ema200_pct: float | None = None
    allowed_directions: list[str] = Field(default_factory=lambda: ["LONG"])
    last_update: str | None = None
    error: str | None = None


# ─────────────────────────────────────────────────────────────────
# /api/equity_curve
# ─────────────────────────────────────────────────────────────────

class EquityPoint(_Base):
    date: str
    ticker: str
    pnl: float
    equity: float
    status: str


class EquityCurveResponse(_Base):
    curve: list[EquityPoint] = Field(default_factory=list)
    final_equity: float
    initial_equity: float


# ─────────────────────────────────────────────────────────────────
# /api/universe
# ─────────────────────────────────────────────────────────────────

class UniverseResponse(_Base):
    version: int | None = None
    updated_at: str | None = None
    # Métadonnées de fraîcheur (W8) — permet au frontend de flagger un cache périmé.
    last_updated_timestamp: float | None = None   # epoch seconds, parsed from updated_at
    stale_days: float | None = None               # age en jours (None si updated_at absent)
    is_stale: bool = False                        # True si stale_days > staleness_threshold_days
    staleness_threshold_days: int = 7
    # Qualité des données (W8) — nombre et fraction de tickers avec market_cap null
    # ou autres champs critiques manquants. Permet à l'UI d'afficher une bannière
    # "X tickers incomplets — rebuilder l'univers".
    incomplete_count: int = 0
    incomplete_ratio: float = 0.0
    source_indices: list[str] = Field(default_factory=list)
    filter: dict[str, Any] = Field(default_factory=dict)
    stats: dict[str, Any] = Field(default_factory=dict)
    sectors: dict[str, Any] = Field(default_factory=dict)
    tickers: dict[str, Any] = Field(default_factory=dict)
    count: int
    rebuilding: bool
    rebuild_job_id: str | None = None
    last_change: Any = None  # dict ou str libre selon le rebuild (note versionnée)


# ─────────────────────────────────────────────────────────────────
# /api/jobs — job metadata
# ─────────────────────────────────────────────────────────────────

class JobMeta(_Base):
    job_id: str
    name: str
    cmd: str
    pid: int
    started: str
    log_path: str | None = None
    running: bool | None = None


class JobOutputResponse(_Base):
    job_id: str
    running: bool
    cmd: str | None = None
    lines: list[str] = Field(default_factory=list)


class JobLaunchResponse(_Base):
    ok: bool
    job: JobMeta


# ─────────────────────────────────────────────────────────────────
# /api/performance_metrics
# ─────────────────────────────────────────────────────────────────

class PerformanceMetricsResponse(_Base):
    capital: float
    pnl_total: float
    wins: int
    losses: int
    total_closed: int
    win_rate: float
    profit_factor: float
    open_count: int
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    expectancy: float = 0.0
    streak: int = 0
    avg_holding_days: float = 0.0
    equity_by_date: list[list[Any]] = Field(default_factory=list)
    pnl_raw: list[float] = Field(default_factory=list)
    pnl_series: list[float] = Field(default_factory=list)
    daily_pnl: dict[str, float] = Field(default_factory=dict)
    per_ticker_pnl: dict[str, dict[str, Any]] = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────
# /api/macro_calendar
# ─────────────────────────────────────────────────────────────────

class MacroCalendarEvent(_Base):
    date: str
    type: str
    label: str
    days_delta: int
    blackout: bool
    upcoming: bool


class MacroCalendarResponse(_Base):
    today: str
    in_blackout: bool
    next_event: MacroCalendarEvent | None = None
    events: list[MacroCalendarEvent] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────
# Generic ok/message response (trade/add, trade/close, job/{id}/kill)
# ─────────────────────────────────────────────────────────────────

class GenericOkResponse(_Base):
    ok: bool
    message: str

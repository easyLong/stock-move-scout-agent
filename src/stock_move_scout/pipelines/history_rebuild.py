from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stock_move_scout.db import MySqlConfig, mysql_rows, run_mysql, sql_string
from stock_move_scout.feed.leaderboard_snapshot import (
    materialize_kpl_leaderboard_snapshot,
    materialize_leaderboard_snapshot,
)
from stock_move_scout.pipelines.runner import PipelineResult, StepResult, run_step
from stock_move_scout.research_pool import (
    DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS,
    DEFAULT_RESEARCH_POOL_GAIN_TOP,
    DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS,
    materialize_research_pool_snapshot,
    materialize_research_pool_theme_members,
    normalize_research_pool_ma_mode,
)
from stock_move_scout.sources.kpl_featured_sections import (
    KplFeaturedSectionConfig,
    collect_kpl_stock_featured_sections,
)
from stock_move_scout.sources.kpl_plate_details import KplPlateDetailConfig, collect_kpl_plate_details
from stock_move_scout.sources.kpl_plate_strength import KplPlateStrengthConfig, collect_kpl_plate_strength


@dataclass(frozen=True)
class HistoryRebuildConfig:
    dates: tuple[str, ...]
    ma_mode: str = "none"
    force: bool = True
    include_kpl: bool = True
    skip_dependency_check: bool = True
    kpl_featured_limit: int = 0
    kpl_featured_timeout: int = 8
    kpl_featured_pause: float = 0.03
    kpl_plate_strength_limit: int = 20
    kpl_plate_strength_timeout: int = 12
    kpl_plate_detail_limit: int = 5
    kpl_plate_detail_timeout: int = 10
    kpl_plate_detail_pause: float = 0.05


def latest_daily_bar_trade_dates(config: MySqlConfig, *, limit: int = 5) -> tuple[str, ...]:
    sql = f"""
    SELECT DATE_FORMAT(trade_date, '%Y-%m-%d')
    FROM stock_daily_bars
    WHERE trade_date <= CURDATE()
    GROUP BY trade_date
    HAVING COUNT(*) >= 1000
    ORDER BY trade_date DESC
    LIMIT {max(1, int(limit))};
    """
    rows = [str(row[0]).strip() for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)) if row and row[0]]
    return tuple(reversed(rows))


def rebuild_trade_date(config: MySqlConfig, trade_date: str, cfg: HistoryRebuildConfig) -> PipelineResult:
    ma_mode = normalize_research_pool_ma_mode(cfg.ma_mode)
    steps: list[StepResult] = []
    steps.append(
        run_step(
            "research_pool",
            lambda: materialize_research_pool_snapshot(
                config,
                trade_date,
                limit_up_days=DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS,
                gain_period_days=DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS,
                gain_top=DEFAULT_RESEARCH_POOL_GAIN_TOP,
                ma_mode=ma_mode,
                force=cfg.force,
            ),
        )
    )
    steps.append(
        run_step(
            "kpl_featured_sections",
            lambda: collect_kpl_stock_featured_sections(
                config,
                KplFeaturedSectionConfig(
                    trade_date=trade_date,
                    timeout=cfg.kpl_featured_timeout,
                    pause=cfg.kpl_featured_pause,
                    limit=cfg.kpl_featured_limit,
                    ma_mode=ma_mode,
                ),
            ),
        )
    )
    steps.append(
        run_step(
            "kpl_plate_strength",
            lambda: collect_kpl_plate_strength(
                config,
                KplPlateStrengthConfig(
                    trade_date=trade_date,
                    limit=cfg.kpl_plate_strength_limit,
                    timeout=cfg.kpl_plate_strength_timeout,
                ),
            ),
        )
    )
    steps.append(
        run_step(
            "kpl_plate_details",
            lambda: collect_kpl_plate_details(
                config,
                KplPlateDetailConfig(
                    trade_date=trade_date,
                    limit=cfg.kpl_plate_detail_limit,
                    timeout=cfg.kpl_plate_detail_timeout,
                    pause=cfg.kpl_plate_detail_pause,
                    ma_mode=ma_mode,
                ),
            ),
        )
    )
    steps.append(
        run_step(
            "research_pool_theme_members",
            lambda: materialize_research_pool_theme_members(config, trade_date, force=cfg.force),
        )
    )
    steps.append(
        run_step(
            "leaderboard_snapshot",
            lambda: materialize_leaderboard_snapshot(
                config,
                trade_date,
                force=cfg.force,
                rebuild_research_pool=False,
                check_dependencies=not cfg.skip_dependency_check,
            ),
        )
    )
    if cfg.include_kpl:
        steps.append(
            run_step(
                "kpl_leaderboard_snapshot",
                lambda: materialize_kpl_leaderboard_snapshot(config, trade_date),
            )
        )
    return PipelineResult(name=f"history_rebuild:{trade_date}", ok=all(step.ok for step in steps), steps=tuple(steps))


def rebuild_history(config: MySqlConfig, cfg: HistoryRebuildConfig) -> PipelineResult:
    steps: list[StepResult] = []
    for trade_date in cfg.dates:
        daily = rebuild_trade_date(config, trade_date, cfg)
        steps.append(
            StepResult(
                name=daily.name,
                ok=daily.ok,
                elapsed_seconds=round(sum(step.elapsed_seconds for step in daily.steps), 2),
                payload=daily.to_dict(),
            )
        )
    return PipelineResult(name="history_rebuild", ok=all(step.ok for step in steps), steps=tuple(steps))


def rebuild_summary_sql(dates: tuple[str, ...]) -> str:
    if not dates:
        return "SELECT JSON_ARRAY();"
    values = ", ".join(sql_string(day) for day in dates)
    return f"""
    SELECT COALESCE(JSON_ARRAYAGG(JSON_OBJECT(
      'trade_date', DATE_FORMAT(d.trade_date, '%Y-%m-%d'),
      'research_pool_count', COALESCE(rp.code_count, 0),
      'ma_mode', COALESCE(JSON_UNQUOTE(JSON_EXTRACT(rp.params_json, '$.ma_mode')), ''),
      'featured_section_count', COALESCE(fs.cnt, 0),
      'plate_strength_count', COALESCE(ps.cnt, 0),
      'plate_detail_count', COALESCE(pd.cnt, 0),
      'leaderboard_snapshot_count', COALESCE(ls.normal_cnt, 0),
      'kpl_leaderboard_snapshot_count', COALESCE(ls.kpl_cnt, 0),
      'close_width_count', COALESCE(mw.cnt, 0)
    )), JSON_ARRAY())
    FROM (
      SELECT CAST(day_value AS DATE) AS trade_date
      FROM (
        SELECT {values.split(', ')[0]} AS day_value
        {' '.join(f"UNION ALL SELECT {value}" for value in values.split(', ')[1:])}
      ) selected_days
    ) d
    LEFT JOIN research_pool_snapshots rp ON rp.trade_date=d.trade_date
    LEFT JOIN (
      SELECT trade_date, COUNT(*) cnt FROM kpl_stock_featured_sections
      WHERE trade_date IN ({values}) GROUP BY trade_date
    ) fs ON fs.trade_date=d.trade_date
    LEFT JOIN (
      SELECT trade_date, COUNT(*) cnt FROM kpl_plate_featured_strengths
      WHERE trade_date IN ({values}) GROUP BY trade_date
    ) ps ON ps.trade_date=d.trade_date
    LEFT JOIN (
      SELECT trade_date, COUNT(*) cnt FROM kpl_plate_featured_details
      WHERE trade_date IN ({values}) GROUP BY trade_date
    ) pd ON pd.trade_date=d.trade_date
    LEFT JOIN (
      SELECT trade_date,
        SUM(source='post_close_confirm') normal_cnt,
        SUM(source='kpl_primary_theme') kpl_cnt
      FROM leaderboard_snapshots WHERE trade_date IN ({values}) GROUP BY trade_date
    ) ls ON ls.trade_date=d.trade_date
    LEFT JOIN (
      SELECT trade_date, COUNT(*) cnt FROM market_width_snapshots
      WHERE trade_date IN ({values}) AND source='stock_daily_bars_close'
      GROUP BY trade_date
    ) mw ON mw.trade_date=d.trade_date;
    """


def rebuild_summary(config: MySqlConfig, dates: tuple[str, ...]) -> list[dict[str, Any]]:
    import json

    output = run_mysql(config, rebuild_summary_sql(dates), batch=True, raw=True)
    if not output:
        return []
    try:
        parsed = json.loads(output)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []

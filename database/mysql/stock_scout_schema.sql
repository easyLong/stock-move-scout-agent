-- Stock Scout Agent schema for MySQL 8.4
-- Purpose: 15s scan -> 10m window ranking -> evidence collection -> concise posts.

CREATE DATABASE IF NOT EXISTS stock_scout
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE stock_scout;

CREATE TABLE IF NOT EXISTS stocks (
  code CHAR(6) NOT NULL COMMENT 'A股代码，如 600157',
  symbol VARCHAR(16) NOT NULL DEFAULT '' COMMENT '交易符号，如 SH600157',
  market VARCHAR(16) NOT NULL DEFAULT '' COMMENT '市场，如 SH/SZ/BJ 或通达信 market',
  name VARCHAR(64) NOT NULL DEFAULT '',
  industry VARCHAR(128) NOT NULL DEFAULT '',
  sub_industry VARCHAR(128) NOT NULL DEFAULT '',
  is_st TINYINT NOT NULL DEFAULT 0,
  official_website VARCHAR(512) NOT NULL DEFAULT '',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (code),
  KEY idx_stocks_name (name),
  KEY idx_stocks_industry (industry)
) ENGINE=InnoDB COMMENT='股票基础信息';

CREATE TABLE IF NOT EXISTS stock_company_profiles (
  code CHAR(6) NOT NULL COMMENT 'A股代码',
  stock_name VARCHAR(64) NOT NULL DEFAULT '' COMMENT '股票名称',
  company_highlights TEXT NULL COMMENT '公司亮点',
  main_business TEXT NULL COMMENT '主营业务',
  sw_industry VARCHAR(128) NOT NULL DEFAULT '' COMMENT '同花顺F10根页面所属申万行业',
  concept_tags TEXT NULL COMMENT '同花顺F10根页面概念贴合度标签',
  latest_management_business_plan MEDIUMTEXT NULL COMMENT 'AI提取最新一期董事会经营评述经营计划',
  PRIMARY KEY (code),
  CONSTRAINT fk_company_profiles_stock
    FOREIGN KEY (code) REFERENCES stocks(code)
    ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='冷数据：公司画像最新快照';

CREATE TABLE IF NOT EXISTS ths_root_snapshots (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  code CHAR(6) NOT NULL COMMENT 'A股代码',
  stock_name VARCHAR(64) NOT NULL DEFAULT '',
  market_id VARCHAR(16) NOT NULL DEFAULT '' COMMENT '同花顺 marketId，如 33/17',
  root_url VARCHAR(512) NOT NULL DEFAULT '',
  fetched_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  source_status VARCHAR(255) NOT NULL DEFAULT '',
  item_count INT NOT NULL DEFAULT 0,
  profile_json JSON NULL COMMENT '根页面公司概要解析结果',
  sections_json JSON NULL COMMENT '根页面各区块摘要，不存整页HTML',
  raw_json JSON NULL COMMENT '抓取元信息和接口返回摘要',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_ths_root_snapshots_code_fetched (code, fetched_at),
  KEY idx_ths_root_snapshots_code_time (code, fetched_at),
  CONSTRAINT fk_ths_root_snapshots_stock
    FOREIGN KEY (code) REFERENCES stocks(code)
    ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='同花顺F10根页面抓取快照';

CREATE TABLE IF NOT EXISTS stock_ths_root_items (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  code CHAR(6) NOT NULL COMMENT 'A股代码',
  stock_name VARCHAR(64) NOT NULL DEFAULT '',
  item_kind ENUM('important_event') NOT NULL DEFAULT 'important_event',
  item_key VARCHAR(64) NOT NULL COMMENT '采集端生成的去重key，建议sha1(kind/date/title/url/content)',
  source_section VARCHAR(64) NOT NULL DEFAULT '' COMMENT '页面区块，如 #pointnew',
  source_rank INT NOT NULL DEFAULT 0 COMMENT '页面内顺序',
  item_date DATE NULL COMMENT '重要事件日期',
  title VARCHAR(512) NOT NULL DEFAULT '',
  content MEDIUMTEXT NULL,
  detail_content MEDIUMTEXT NULL COMMENT '重要事件展开详情',
  url VARCHAR(1024) NOT NULL DEFAULT '',
  tags JSON NULL COMMENT '重要事件标签',
  importance TINYINT NOT NULL DEFAULT 0 COMMENT '预留：0未知，1低，2中，3高',
  source_status VARCHAR(255) NOT NULL DEFAULT '',
  raw_json JSON NULL,
  collected_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_ths_root_items_code_kind_key (code, item_kind, item_key),
  KEY idx_ths_root_items_code_kind_date (code, item_kind, item_date),
  KEY idx_ths_root_items_kind_date (item_kind, item_date),
  KEY idx_ths_root_items_collected (collected_at),
  CONSTRAINT fk_ths_root_items_stock
    FOREIGN KEY (code) REFERENCES stocks(code)
    ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='同花顺F10根页面重要事件';

CREATE TABLE IF NOT EXISTS stock_effective_fact_rules (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  rule_group VARCHAR(64) NOT NULL,
  rule_type VARCHAR(64) NOT NULL,
  pattern VARCHAR(512) NOT NULL,
  enabled TINYINT NOT NULL DEFAULT 1,
  note VARCHAR(255) NOT NULL DEFAULT '',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_effective_fact_rule (rule_group, rule_type, pattern),
  KEY idx_effective_fact_rule_lookup (rule_group, rule_type, enabled)
) ENGINE=InnoDB COMMENT='有效事实过滤规则：龙虎榜强规则、低价值标题过滤等';

CREATE OR REPLACE VIEW stock_current_effective_facts_view AS
SELECT
  i.code,
  i.stock_name,
  'stock_ths_root_items' AS source_table,
  CONCAT('stock_ths_root_items:', i.id) AS source_key,
  'important_event' AS fact_type,
  COALESCE(JSON_UNQUOTE(JSON_EXTRACT(i.tags, '$[0]')), '') AS fact_subtype,
  i.title AS fact_title,
  LEFT(
    CONCAT_WS(' ',
      COALESCE(NULLIF(i.content, ''), i.title),
      NULLIF(i.detail_content, '')
    ),
    1200
  ) AS fact_body,
  i.item_date AS fact_date,
  i.item_date AS valid_from,
  DATE_ADD(i.item_date, INTERVAL 10 DAY) AS valid_until,
  JSON_OBJECT(
    'root_item_id', i.id,
    'item_key', i.item_key,
    'source_section', i.source_section,
    'source_rank', i.source_rank,
    'url', i.url,
    'detail_content', COALESCE(i.detail_content, ''),
    'tags', COALESCE(i.tags, JSON_ARRAY()),
    'raw_json', COALESCE(i.raw_json, JSON_OBJECT()),
    'rule', 'recent_important_event_10d'
  ) AS payload
FROM stock_ths_root_items i
WHERE i.item_kind='important_event'
  AND i.item_date IS NOT NULL
  AND NOT (COALESCE(i.title, '') REGEXP '^融资融券$|^发布公告$|^投资互动$|^股东人数变化$|^股东大会$|^分配预案$|^实施分红$|^异动提醒$|^大宗交易$');

CREATE TABLE IF NOT EXISTS stock_daily_bars (
  code CHAR(6) NOT NULL,
  trade_date DATE NOT NULL,
  stock_name VARCHAR(64) NOT NULL DEFAULT '',
  open_price DECIMAL(12,4) NULL,
  high_price DECIMAL(12,4) NULL,
  low_price DECIMAL(12,4) NULL,
  close_price DECIMAL(12,4) NULL,
  pct_change DECIMAL(10,4) NULL,
  volume BIGINT NULL,
  amount DECIMAL(20,2) NULL,
  source VARCHAR(64) NOT NULL DEFAULT 'akshare_stock_zh_a_hist',
  raw_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (code, trade_date),
  KEY idx_stock_daily_bars_day (trade_date),
  KEY idx_stock_daily_bars_code_day (code, trade_date)
) ENGINE=InnoDB COMMENT='Daily stock bars for derived evidence validation';

CREATE TABLE IF NOT EXISTS stock_effective_facts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  trade_date DATE NOT NULL,
  code CHAR(6) NOT NULL,
  stock_name VARCHAR(64) NOT NULL DEFAULT '',
  source_table VARCHAR(64) NOT NULL,
  source_key VARCHAR(128) NOT NULL,
  source_confidence VARCHAR(32) NOT NULL DEFAULT 'explicit',
  fact_type VARCHAR(32) NOT NULL DEFAULT '',
  fact_subtype VARCHAR(64) NOT NULL DEFAULT '',
  fact_title VARCHAR(512) NOT NULL DEFAULT '',
  fact_body TEXT NULL,
  fact_date DATE NULL,
  valid_status ENUM('active','watch','historical','expired','invalid') NOT NULL DEFAULT 'watch',
  valid_score DECIMAL(8,2) NOT NULL DEFAULT 0,
  valid_reason VARCHAR(255) NOT NULL DEFAULT '',
  invalid_reason VARCHAR(255) NOT NULL DEFAULT '',
  evidence_role VARCHAR(64) NOT NULL DEFAULT '',
  evidence_group ENUM('current_effective','post_close_confirm','background_fact','historical_tag','hidden') NOT NULL DEFAULT 'background_fact',
  display_level ENUM('primary','secondary','background','hidden') NOT NULL DEFAULT 'secondary',
  valid_from DATE NULL,
  valid_until DATE NULL,
  payload JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_effective_fact_source (trade_date, source_table, source_key),
  KEY idx_effective_fact_code_day (code, trade_date, display_level, valid_score),
  KEY idx_effective_fact_group (trade_date, evidence_group, display_level, valid_score),
  KEY idx_effective_fact_role (trade_date, evidence_role, display_level),
  KEY idx_effective_fact_source_table (source_table, source_key)
) ENGINE=InnoDB COMMENT='有效事实层：原始事实经过时效/有效性过滤后的证据候选';

CREATE TABLE IF NOT EXISTS stock_effective_facts_dirty_queue (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  trade_date DATE NOT NULL,
  code CHAR(6) NOT NULL,
  stock_name VARCHAR(64) NOT NULL DEFAULT '',
  reason VARCHAR(255) NOT NULL DEFAULT '',
  changed_sources JSON NULL,
  priority INT NOT NULL DEFAULT 35,
  status ENUM('pending','running','done','failed','ignored') NOT NULL DEFAULT 'pending',
  attempt_count INT NOT NULL DEFAULT 0,
  locked_at DATETIME(3) NULL,
  finished_at DATETIME(3) NULL,
  last_error TEXT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_effective_facts_dirty (trade_date, code, reason),
  KEY idx_effective_facts_dirty_status (status, priority, created_at),
  KEY idx_effective_facts_dirty_code (code, trade_date)
) ENGINE=InnoDB COMMENT='原始事实变更后，先重建对应股票有效事实的 dirty 队列';

CREATE TABLE IF NOT EXISTS stock_root_evidence_cache (
  trade_date DATE NOT NULL,
  code CHAR(6) NOT NULL,
  items JSON NOT NULL,
  root_count INT NOT NULL DEFAULT 0,
  latest_source_updated_at DATETIME(3) NULL,
  generated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (trade_date, code),
  KEY idx_root_evidence_cache_code_day (code, trade_date),
  KEY idx_root_evidence_cache_generated (generated_at)
) ENGINE=InnoDB COMMENT='按股票预聚合后的证据详情缓存';

CREATE TABLE IF NOT EXISTS stock_root_evidence_cache_dirty_queue (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  trade_date DATE NOT NULL,
  code CHAR(6) NOT NULL,
  stock_name VARCHAR(64) NOT NULL DEFAULT '',
  reason VARCHAR(255) NOT NULL DEFAULT '',
  changed_sources JSON NULL,
  priority INT NOT NULL DEFAULT 40,
  status ENUM('pending','running','done','failed','ignored') NOT NULL DEFAULT 'pending',
  attempt_count INT NOT NULL DEFAULT 0,
  locked_at DATETIME(3) NULL,
  finished_at DATETIME(3) NULL,
  last_error TEXT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_root_evidence_dirty (trade_date, code, reason),
  KEY idx_root_evidence_dirty_status (status, priority, created_at),
  KEY idx_root_evidence_dirty_code (code, trade_date)
) ENGINE=InnoDB COMMENT='原始事实变更后，仅刷新对应股票证据缓存的 dirty 队列';

CREATE TABLE IF NOT EXISTS stock_move_events (
  event_id VARCHAR(128) NOT NULL PRIMARY KEY,
  trade_date DATE NOT NULL,
  event_time DATETIME(3) NOT NULL,
  code CHAR(6) NOT NULL,
  stock_name VARCHAR(64) NOT NULL DEFAULT '',
  event_type VARCHAR(32) NOT NULL DEFAULT '',
  source_table VARCHAR(64) NOT NULL DEFAULT '',
  source_key VARCHAR(128) NOT NULL DEFAULT '',
  trigger_price DECIMAL(12,4) NULL,
  trigger_pct DECIMAL(10,4) NULL,
  speed_pct DECIMAL(10,4) NULL,
  amount DECIMAL(20,2) NULL,
  sort_rank INT NOT NULL DEFAULT 0,
  anchor_scope_type VARCHAR(32) NOT NULL DEFAULT '',
  anchor_name VARCHAR(128) NOT NULL DEFAULT '',
  role_label VARCHAR(64) NOT NULL DEFAULT '',
  role_score DECIMAL(14,4) NOT NULL DEFAULT 0,
  event_strength DECIMAL(14,4) NOT NULL DEFAULT 0,
  payload JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_stock_move_event_source (source_table, source_key, code),
  KEY idx_stock_move_events_day_time (trade_date, event_time),
  KEY idx_stock_move_events_code_day (code, trade_date),
  KEY idx_stock_move_events_anchor (trade_date, anchor_name, event_strength),
  KEY idx_stock_move_events_type (trade_date, event_type, event_strength)
) ENGINE=InnoDB COMMENT='标准化异动事件层：实时领涨、稳定异动、竞价等统一入口';

CREATE TABLE IF NOT EXISTS derived_signals (
  signal_id VARCHAR(160) NOT NULL PRIMARY KEY,
  trade_date DATE NOT NULL,
  signal_time DATETIME(3) NOT NULL,
  scope_type VARCHAR(32) NOT NULL DEFAULT '',
  scope_key VARCHAR(128) NOT NULL DEFAULT '',
  related_event_id VARCHAR(128) NOT NULL DEFAULT '',
  code CHAR(6) NOT NULL DEFAULT '',
  signal_type VARCHAR(64) NOT NULL DEFAULT '',
  signal_name VARCHAR(128) NOT NULL DEFAULT '',
  signal_value VARCHAR(255) NOT NULL DEFAULT '',
  signal_stage VARCHAR(64) NOT NULL DEFAULT '',
  signal_score DECIMAL(14,4) NOT NULL DEFAULT 0,
  source_table VARCHAR(64) NOT NULL DEFAULT '',
  source_key VARCHAR(128) NOT NULL DEFAULT '',
  source_tables JSON NULL,
  payload JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_derived_signal_source (source_table, source_key, signal_type, scope_type, scope_key, code),
  KEY idx_derived_signals_scope (trade_date, scope_type, scope_key, signal_type, signal_time),
  KEY idx_derived_signals_event (related_event_id),
  KEY idx_derived_signals_code (code, trade_date, signal_type)
) ENGINE=InnoDB COMMENT='派生信号层：个股角色、板块周期、情绪周期等可扩展证据状态';

CREATE TABLE IF NOT EXISTS stock_move_evidence (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  event_id VARCHAR(128) NOT NULL,
  trade_date DATE NOT NULL,
  event_time DATETIME(3) NOT NULL,
  code CHAR(6) NOT NULL,
  stock_name VARCHAR(64) NOT NULL DEFAULT '',
  evidence_scope_type VARCHAR(32) NOT NULL DEFAULT 'stock',
  evidence_scope_key VARCHAR(128) NOT NULL DEFAULT '',
  evidence_type VARCHAR(64) NOT NULL DEFAULT '',
  evidence_group ENUM('current_effective','post_close_confirm','background_fact','historical_tag','hidden') NOT NULL DEFAULT 'current_effective',
  evidence_role VARCHAR(64) NOT NULL DEFAULT '',
  source_table VARCHAR(64) NOT NULL DEFAULT '',
  source_key VARCHAR(160) NOT NULL DEFAULT '',
  evidence_title VARCHAR(512) NOT NULL DEFAULT '',
  evidence_body TEXT NULL,
  relevance_score DECIMAL(8,2) NOT NULL DEFAULT 0,
  validity_score DECIMAL(8,2) NOT NULL DEFAULT 0,
  display_priority INT NOT NULL DEFAULT 100,
  model_required TINYINT NOT NULL DEFAULT 0,
  payload JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_move_evidence_source (event_id, source_table, source_key, evidence_type),
  KEY idx_move_evidence_event (event_id, display_priority),
  KEY idx_move_evidence_code_day (code, trade_date, evidence_group, display_priority),
  KEY idx_move_evidence_source (source_table, source_key)
) ENGINE=InnoDB COMMENT='事件级证据层：针对一次异动匹配事实和信号';

CREATE TABLE IF NOT EXISTS stock_move_evidence_dirty_queue (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  trade_date DATE NOT NULL,
  event_id VARCHAR(128) NOT NULL DEFAULT '',
  code CHAR(6) NOT NULL DEFAULT '',
  reason VARCHAR(255) NOT NULL DEFAULT '',
  changed_sources JSON NULL,
  priority INT NOT NULL DEFAULT 40,
  status ENUM('pending','running','done','failed','ignored') NOT NULL DEFAULT 'pending',
  attempt_count INT NOT NULL DEFAULT 0,
  locked_at DATETIME(3) NULL,
  finished_at DATETIME(3) NULL,
  last_error TEXT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_move_evidence_dirty (trade_date, event_id, code, reason),
  KEY idx_move_evidence_dirty_status (status, priority, created_at),
  KEY idx_move_evidence_dirty_code (code, trade_date)
) ENGINE=InnoDB COMMENT='事件证据重建队列：有效事实或信号变化后按事件/股票增量刷新';

CREATE TABLE IF NOT EXISTS scan_runs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  run_id VARCHAR(32) NOT NULL COMMENT '单次扫描 ID',
  scanned_at DATETIME(3) NOT NULL,
  source VARCHAR(64) NOT NULL DEFAULT 'tdx_mover_watcher',
  scan_top INT NOT NULL DEFAULT 10,
  market_phase VARCHAR(32) NOT NULL DEFAULT '',
  accepted TINYINT NOT NULL DEFAULT 0 COMMENT '是否纳入窗口统计',
  ok TINYINT NOT NULL DEFAULT 0,
  return_code INT NOT NULL DEFAULT 0,
  duration_ms INT NOT NULL DEFAULT 0,
  row_count INT NOT NULL DEFAULT 0,
  preserve_last TINYINT NOT NULL DEFAULT 0,
  restored TINYINT NOT NULL DEFAULT 0,
  error_text TEXT NULL,
  raw_meta JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_scan_runs_run_id (run_id),
  KEY idx_scan_runs_time (scanned_at),
  KEY idx_scan_runs_accepted_time (accepted, scanned_at)
) ENGINE=InnoDB COMMENT='每 15 秒通达信扫描记录';

CREATE TABLE IF NOT EXISTS scan_movers (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  scan_run_id BIGINT UNSIGNED NOT NULL,
  captured_at DATETIME(3) NOT NULL,
  code CHAR(6) NOT NULL,
  name VARCHAR(64) NOT NULL DEFAULT '',
  rank_speed INT NOT NULL DEFAULT 0,
  rank_pct_change INT NOT NULL DEFAULT 0,
  price DECIMAL(12,4) NULL,
  speed DECIMAL(10,4) NULL COMMENT '涨速',
  pct_change DECIMAL(10,4) NULL COMMENT '涨幅',
  amount DECIMAL(20,2) NULL COMMENT '成交额',
  amount_delta_15s DECIMAL(20,2) NULL COMMENT '15s amount increment used by mover signal filter',
  volume BIGINT NULL,
  volume_delta_15s BIGINT NULL COMMENT '15s volume increment used by mover signal filter',
  current_volume BIGINT NULL,
  bid1 DECIMAL(12,4) NULL,
  ask1 DECIMAL(12,4) NULL,
  industry VARCHAR(128) NOT NULL DEFAULT '',
  sub_industry VARCHAR(128) NOT NULL DEFAULT '',
  concepts JSON NULL,
  basis VARCHAR(64) NOT NULL DEFAULT '',
  raw_row JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_scan_movers_run_code (scan_run_id, code),
  KEY idx_scan_movers_code_time (code, captured_at),
  KEY idx_scan_movers_run_rank (scan_run_id, rank_speed),
  KEY idx_scan_movers_speed (captured_at, speed),
  KEY idx_scan_movers_amount_delta (captured_at, amount_delta_15s),
  CONSTRAINT fk_scan_movers_scan_run
    FOREIGN KEY (scan_run_id) REFERENCES scan_runs(id)
    ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='单次扫描 TopN 明细';

CREATE TABLE IF NOT EXISTS market_width_snapshots (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  snapshot_id VARCHAR(32) NOT NULL,
  trade_date DATE NOT NULL,
  captured_at DATETIME(3) NOT NULL,
  source VARCHAR(64) NOT NULL DEFAULT 'akshare_stock_zh_a_spot',
  market_scope VARCHAR(32) NOT NULL DEFAULT 'cn_a_main',
  total_count INT NOT NULL DEFAULT 0,
  up_count INT NOT NULL DEFAULT 0,
  down_count INT NOT NULL DEFAULT 0,
  flat_count INT NOT NULL DEFAULT 0,
  up3_count INT NOT NULL DEFAULT 0,
  down3_count INT NOT NULL DEFAULT 0,
  up5_count INT NOT NULL DEFAULT 0,
  down5_count INT NOT NULL DEFAULT 0,
  limit_up_count INT NOT NULL DEFAULT 0,
  limit_down_count INT NOT NULL DEFAULT 0,
  amount_top50_count INT NOT NULL DEFAULT 0,
  amount_top50_up_count INT NOT NULL DEFAULT 0,
  amount_top50_down_count INT NOT NULL DEFAULT 0,
  amount_top50_flat_count INT NOT NULL DEFAULT 0,
  amount_top50_up3_count INT NOT NULL DEFAULT 0,
  amount_top50_down3_count INT NOT NULL DEFAULT 0,
  amount_top50_up5_count INT NOT NULL DEFAULT 0,
  amount_top50_down5_count INT NOT NULL DEFAULT 0,
  research_pool_trade_date DATE NULL,
  research_pool_rule VARCHAR(64) NOT NULL DEFAULT '',
  research_pool_count INT NOT NULL DEFAULT 0,
  research_pool_up_count INT NOT NULL DEFAULT 0,
  research_pool_down_count INT NOT NULL DEFAULT 0,
  research_pool_flat_count INT NOT NULL DEFAULT 0,
  research_pool_up3_count INT NOT NULL DEFAULT 0,
  research_pool_down3_count INT NOT NULL DEFAULT 0,
  research_pool_up5_count INT NOT NULL DEFAULT 0,
  research_pool_down5_count INT NOT NULL DEFAULT 0,
  sh_index_price DECIMAL(12,4) NULL,
  sh_index_pct_change DECIMAL(10,4) NULL,
  sh_index_amount DECIMAL(24,2) NULL,
  sh_index_volume BIGINT NULL,
  total_volume BIGINT NULL,
  total_amount DECIMAL(24,2) NOT NULL DEFAULT 0,
  top50_amount DECIMAL(24,2) NOT NULL DEFAULT 0,
  raw_meta JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_market_width_snapshot_id (snapshot_id),
  KEY idx_market_width_trade_time (trade_date, captured_at),
  KEY idx_market_width_created_at (created_at)
) ENGINE=InnoDB COMMENT='盘中市场概览快照：全市场、成交额Top50、研究池宽度统计';

CREATE TABLE IF NOT EXISTS market_width_amount_top50 (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  snapshot_id VARCHAR(32) NOT NULL,
  trade_date DATE NOT NULL,
  captured_at DATETIME(3) NOT NULL,
  rank_no INT NOT NULL DEFAULT 0,
  code CHAR(6) NOT NULL,
  name VARCHAR(64) NOT NULL DEFAULT '',
  latest_price DECIMAL(12,4) NULL,
  pct_change DECIMAL(10,4) NULL,
  amount DECIMAL(20,2) NULL,
  volume BIGINT NULL,
  raw_row JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_market_width_top_snapshot_code (snapshot_id, code),
  KEY idx_market_width_top_trade_rank (trade_date, captured_at, rank_no),
  KEY idx_market_width_top_code_time (code, captured_at),
  CONSTRAINT fk_market_width_top_snapshot
    FOREIGN KEY (snapshot_id) REFERENCES market_width_snapshots(snapshot_id)
    ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='盘中成交额最大Top50快照';

CREATE TABLE IF NOT EXISTS scan_anchor_stats (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  scan_run_id BIGINT UNSIGNED NOT NULL,
  rank_no INT NOT NULL DEFAULT 0,
  anchor_type VARCHAR(32) NOT NULL DEFAULT '',
  anchor_name VARCHAR(128) NOT NULL DEFAULT '',
  member_count INT NOT NULL DEFAULT 0,
  leader_code CHAR(6) NOT NULL DEFAULT '',
  leader_name VARCHAR(64) NOT NULL DEFAULT '',
  core_code CHAR(6) NOT NULL DEFAULT '',
  core_name VARCHAR(64) NOT NULL DEFAULT '',
  total_amount DECIMAL(20,2) NOT NULL DEFAULT 0,
  max_pct_change DECIMAL(10,4) NOT NULL DEFAULT 0,
  avg_pct_change DECIMAL(10,4) NOT NULL DEFAULT 0,
  max_speed DECIMAL(10,4) NOT NULL DEFAULT 0,
  avg_speed DECIMAL(10,4) NOT NULL DEFAULT 0,
  anchor_score DECIMAL(14,4) NOT NULL DEFAULT 0,
  strength_label VARCHAR(64) NOT NULL DEFAULT '',
  raw_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_scan_anchor_stats_key (scan_run_id, anchor_type, anchor_name),
  KEY idx_scan_anchor_stats_rank (scan_run_id, rank_no),
  KEY idx_scan_anchor_stats_score (scan_run_id, anchor_score),
  CONSTRAINT fk_scan_anchor_stats_scan_run
    FOREIGN KEY (scan_run_id) REFERENCES scan_runs(id)
    ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='单次扫描锚点统计：用于实时领涨和锚点摘要';

CREATE TABLE IF NOT EXISTS scan_stock_roles (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  scan_run_id BIGINT UNSIGNED NOT NULL,
  code CHAR(6) NOT NULL,
  name VARCHAR(64) NOT NULL DEFAULT '',
  rank_no INT NOT NULL DEFAULT 0,
  primary_anchor_type VARCHAR(32) NOT NULL DEFAULT '',
  primary_anchor_name VARCHAR(128) NOT NULL DEFAULT '',
  anchor_member_count INT NOT NULL DEFAULT 0,
  role_label VARCHAR(64) NOT NULL DEFAULT '',
  leader_code CHAR(6) NOT NULL DEFAULT '',
  leader_name VARCHAR(64) NOT NULL DEFAULT '',
  core_code CHAR(6) NOT NULL DEFAULT '',
  core_name VARCHAR(64) NOT NULL DEFAULT '',
  role_score DECIMAL(14,4) NOT NULL DEFAULT 0,
  role_reason VARCHAR(512) NOT NULL DEFAULT '',
  raw_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_scan_stock_roles_code (scan_run_id, code),
  KEY idx_scan_stock_roles_role (scan_run_id, role_label),
  KEY idx_scan_stock_roles_anchor (scan_run_id, primary_anchor_name),
  KEY idx_scan_stock_roles_score (scan_run_id, role_score),
  CONSTRAINT fk_scan_stock_roles_scan_run
    FOREIGN KEY (scan_run_id) REFERENCES scan_runs(id)
    ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='单次扫描个股角色：领涨、中军、跟风、孤立脉冲';

CREATE TABLE IF NOT EXISTS windows (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  window_id VARCHAR(32) NOT NULL COMMENT '5分钟窗口 ID，如 20260507_134934',
  started_at DATETIME(3) NOT NULL,
  ended_at DATETIME(3) NOT NULL,
  scan_interval_seconds INT NOT NULL DEFAULT 15,
  window_seconds INT NOT NULL DEFAULT 300,
  target_scan_count INT NOT NULL DEFAULT 40,
  accepted_scan_count INT NOT NULL DEFAULT 0,
  min_accepted_scan_count INT NOT NULL DEFAULT 3,
  status ENUM('building','done','skipped','failed') NOT NULL DEFAULT 'done',
  aggregate_count INT NOT NULL DEFAULT 0,
  evidence_candidate_count INT NOT NULL DEFAULT 0,
  duration_ms INT NOT NULL DEFAULT 0,
  snapshot_dir VARCHAR(1024) NOT NULL DEFAULT '',
  raw_meta JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_windows_window_id (window_id),
  KEY idx_windows_time (started_at, ended_at),
  KEY idx_windows_status_time (status, ended_at)
) ENGINE=InnoDB COMMENT='5分钟聚合窗口';

CREATE TABLE IF NOT EXISTS window_scans (
  window_id BIGINT UNSIGNED NOT NULL,
  scan_run_id BIGINT UNSIGNED NOT NULL,
  accepted TINYINT NOT NULL DEFAULT 0,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (window_id, scan_run_id),
  KEY idx_window_scans_scan (scan_run_id),
  CONSTRAINT fk_window_scans_window
    FOREIGN KEY (window_id) REFERENCES windows(id)
    ON DELETE CASCADE,
  CONSTRAINT fk_window_scans_scan
    FOREIGN KEY (scan_run_id) REFERENCES scan_runs(id)
    ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='窗口与扫描的对应关系';

CREATE TABLE IF NOT EXISTS window_movers (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  window_id BIGINT UNSIGNED NOT NULL,
  code CHAR(6) NOT NULL,
  name VARCHAR(64) NOT NULL DEFAULT '',
  rank_no INT NOT NULL DEFAULT 0,
  appearance_count INT NOT NULL DEFAULT 0,
  appearance_rate DECIMAL(8,4) NOT NULL DEFAULT 0,
  best_rank_speed INT NOT NULL DEFAULT 0,
  avg_rank_speed DECIMAL(10,4) NOT NULL DEFAULT 0,
  max_speed DECIMAL(10,4) NULL,
  max_pct_change DECIMAL(10,4) NULL,
  latest_price DECIMAL(12,4) NULL,
  latest_pct_change DECIMAL(10,4) NULL,
  amount DECIMAL(20,2) NULL,
  max_amount_delta_15s DECIMAL(20,2) NULL,
  max_volume_delta_15s BIGINT NULL,
  first_seen_at DATETIME(3) NULL,
  latest_seen_at DATETIME(3) NULL,
  previous_window_rank INT NULL,
  rank_delta INT NULL,
  is_new_entry TINYINT NOT NULL DEFAULT 0,
  burst_score DECIMAL(14,4) NOT NULL DEFAULT 0,
  sustained_score DECIMAL(14,4) NOT NULL DEFAULT 0,
  window_score DECIMAL(14,4) NOT NULL DEFAULT 0,
  raw_row JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_window_movers_window_code (window_id, code),
  KEY idx_window_movers_rank (window_id, rank_no),
  KEY idx_window_movers_code_time (code, window_id),
  KEY idx_window_movers_score (window_id, window_score),
  CONSTRAINT fk_window_movers_window
    FOREIGN KEY (window_id) REFERENCES windows(id)
    ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='窗口聚合 TopN，真正用于排序选股';

CREATE TABLE IF NOT EXISTS window_sector_stats (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  window_id BIGINT UNSIGNED NOT NULL,
  rank_no INT NOT NULL DEFAULT 0,
  sector_key VARCHAR(128) NOT NULL DEFAULT '',
  sector_type VARCHAR(32) NOT NULL DEFAULT '',
  stock_count INT NOT NULL DEFAULT 0,
  leader_code CHAR(6) NOT NULL DEFAULT '',
  leader_name VARCHAR(64) NOT NULL DEFAULT '',
  core_code CHAR(6) NOT NULL DEFAULT '',
  core_name VARCHAR(64) NOT NULL DEFAULT '',
  follower_count INT NOT NULL DEFAULT 0,
  total_amount DECIMAL(20,2) NOT NULL DEFAULT 0,
  avg_pct_change DECIMAL(10,4) NOT NULL DEFAULT 0,
  max_speed DECIMAL(10,4) NOT NULL DEFAULT 0,
  sector_score DECIMAL(14,4) NOT NULL DEFAULT 0,
  strength_label VARCHAR(64) NOT NULL DEFAULT '',
  hot_concepts JSON NULL,
  summary VARCHAR(512) NOT NULL DEFAULT '',
  raw_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_window_sector_stats_key (window_id, sector_key),
  KEY idx_window_sector_stats_rank (window_id, rank_no),
  KEY idx_window_sector_stats_score (window_id, sector_score),
  CONSTRAINT fk_window_sector_stats_window
    FOREIGN KEY (window_id) REFERENCES windows(id)
    ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='Window sector role stats';

CREATE TABLE IF NOT EXISTS window_stock_roles (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  window_id BIGINT UNSIGNED NOT NULL,
  code CHAR(6) NOT NULL,
  name VARCHAR(64) NOT NULL DEFAULT '',
  rank_no INT NOT NULL DEFAULT 0,
  sector_key VARCHAR(128) NOT NULL DEFAULT '',
  sector_type VARCHAR(32) NOT NULL DEFAULT '',
  sector_stock_count INT NOT NULL DEFAULT 0,
  role_label VARCHAR(64) NOT NULL DEFAULT '',
  role_score DECIMAL(14,4) NOT NULL DEFAULT 0,
  role_reason VARCHAR(512) NOT NULL DEFAULT '',
  risk_flags VARCHAR(512) NOT NULL DEFAULT '',
  raw_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_window_stock_roles_code (window_id, code),
  KEY idx_window_stock_roles_role (window_id, role_label),
  KEY idx_window_stock_roles_score (window_id, role_score),
  KEY idx_window_stock_roles_sector (window_id, sector_key),
  CONSTRAINT fk_window_stock_roles_window
    FOREIGN KEY (window_id) REFERENCES windows(id)
    ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='Window stock roles';

CREATE TABLE IF NOT EXISTS evidence_candidates (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  window_id BIGINT UNSIGNED NOT NULL,
  code CHAR(6) NOT NULL,
  rank_no INT NOT NULL DEFAULT 0,
  selection_reason VARCHAR(255) NOT NULL DEFAULT '',
  min_pct_pass TINYINT NOT NULL DEFAULT 0,
  is_st TINYINT NOT NULL DEFAULT 0,
  status ENUM('pending','running','done','skipped','failed') NOT NULL DEFAULT 'pending',
  skip_reason VARCHAR(255) NOT NULL DEFAULT '',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_evidence_candidates_window_code (window_id, code),
  KEY idx_evidence_candidates_status (status, updated_at),
  CONSTRAINT fk_evidence_candidates_window
    FOREIGN KEY (window_id) REFERENCES windows(id)
    ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='证据候选池，与窗口榜分开';

CREATE TABLE IF NOT EXISTS evidence_jobs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  job_id VARCHAR(40) NOT NULL,
  window_id BIGINT UNSIGNED NOT NULL,
  status ENUM('pending','running','done','skipped','failed','cancelled') NOT NULL DEFAULT 'pending',
  priority INT NOT NULL DEFAULT 100,
  queued_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  started_at DATETIME(3) NULL,
  finished_at DATETIME(3) NULL,
  worker_pid INT NULL,
  retry_count INT NOT NULL DEFAULT 0,
  max_retries INT NOT NULL DEFAULT 2,
  timeout_seconds INT NOT NULL DEFAULT 600,
  error_text TEXT NULL,
  request_json JSON NULL,
  result_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_evidence_jobs_job_id (job_id),
  UNIQUE KEY uk_evidence_jobs_window (window_id),
  KEY idx_evidence_jobs_pick (status, priority, queued_at),
  CONSTRAINT fk_evidence_jobs_window
    FOREIGN KEY (window_id) REFERENCES windows(id)
    ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='证据 worker 队列，替代 evidence_queue.jsonl';

CREATE TABLE IF NOT EXISTS community_posts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  code CHAR(6) NOT NULL,
  platform VARCHAR(32) NOT NULL DEFAULT 'xueqiu',
  post_id VARCHAR(128) NOT NULL DEFAULT '',
  url VARCHAR(1024) NOT NULL DEFAULT '',
  author_name VARCHAR(128) NOT NULL DEFAULT '',
  author_id VARCHAR(128) NOT NULL DEFAULT '',
  title VARCHAR(512) NOT NULL DEFAULT '',
  content MEDIUMTEXT NULL,
  post_time DATETIME(3) NULL,
  like_count INT NOT NULL DEFAULT 0,
  comment_count INT NOT NULL DEFAULT 0,
  repost_count INT NOT NULL DEFAULT 0,
  raw_json JSON NULL,
  collected_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_community_posts_platform_post (platform, post_id),
  KEY idx_community_posts_code_time (code, post_time),
  KEY idx_community_posts_collected (collected_at)
) ENGINE=InnoDB COMMENT='雪球/社区帖子原文';

CREATE TABLE IF NOT EXISTS community_evidence (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  window_id BIGINT UNSIGNED NOT NULL,
  code CHAR(6) NOT NULL,
  main_claim VARCHAR(512) NOT NULL DEFAULT '',
  trigger_claim VARCHAR(512) NOT NULL DEFAULT '',
  trigger_event VARCHAR(512) NOT NULL DEFAULT '',
  trigger_timing VARCHAR(128) NOT NULL DEFAULT '',
  imagination_path TEXT NULL,
  verification_anchor TEXT NULL,
  support_points JSON NULL,
  disagreements JSON NULL,
  risk_flags JSON NULL,
  hot_terms JSON NULL,
  post_count INT NOT NULL DEFAULT 0,
  signal_quality ENUM('missing','weak','medium','strong') NOT NULL DEFAULT 'missing',
  status ENUM('missing','collected','summarized','failed') NOT NULL DEFAULT 'missing',
  raw_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_community_evidence_window_code (window_id, code),
  KEY idx_community_evidence_quality (signal_quality),
  CONSTRAINT fk_community_evidence_window
    FOREIGN KEY (window_id) REFERENCES windows(id)
    ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='社区内容提炼后的解释线索';

CREATE TABLE IF NOT EXISTS community_evidence_posts (
  community_evidence_id BIGINT UNSIGNED NOT NULL,
  community_post_id BIGINT UNSIGNED NOT NULL,
  relevance_score DECIMAL(8,4) NOT NULL DEFAULT 0,
  quote_digest VARCHAR(512) NOT NULL DEFAULT '',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (community_evidence_id, community_post_id),
  KEY idx_community_evidence_posts_post (community_post_id),
  CONSTRAINT fk_community_evidence_posts_evidence
    FOREIGN KEY (community_evidence_id) REFERENCES community_evidence(id)
    ON DELETE CASCADE,
  CONSTRAINT fk_community_evidence_posts_post
    FOREIGN KEY (community_post_id) REFERENCES community_posts(id)
    ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='社区解释与原帖的引用关系';

CREATE TABLE IF NOT EXISTS official_evidence (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  code CHAR(6) NOT NULL,
  source_type ENUM('cninfo','exchange','official_site','irm','news','policy','ths','other') NOT NULL DEFAULT 'other',
  title VARCHAR(512) NOT NULL DEFAULT '',
  summary TEXT NULL,
  url VARCHAR(1024) NOT NULL DEFAULT '',
  published_at DATETIME(3) NULL,
  evidence_type VARCHAR(128) NOT NULL DEFAULT '',
  strength ENUM('missing','weak','medium','strong') NOT NULL DEFAULT 'missing',
  raw_json JSON NULL,
  collected_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_official_evidence_source_url (source_type, url(700)),
  KEY idx_official_evidence_code_time (code, published_at),
  KEY idx_official_evidence_strength (strength)
) ENGINE=InnoDB COMMENT='公告、官网、新闻、互动易等硬证据';

CREATE TABLE IF NOT EXISTS window_official_evidence (
  window_id BIGINT UNSIGNED NOT NULL,
  official_evidence_id BIGINT UNSIGNED NOT NULL,
  code CHAR(6) NOT NULL,
  relevance_score DECIMAL(8,4) NOT NULL DEFAULT 0,
  usage_note VARCHAR(512) NOT NULL DEFAULT '',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (window_id, official_evidence_id),
  KEY idx_window_official_evidence_code (code),
  KEY idx_window_official_evidence_evidence (official_evidence_id),
  CONSTRAINT fk_window_official_evidence_window
    FOREIGN KEY (window_id) REFERENCES windows(id)
    ON DELETE CASCADE,
  CONSTRAINT fk_window_official_evidence_evidence
    FOREIGN KEY (official_evidence_id) REFERENCES official_evidence(id)
    ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='窗口判断与硬证据的引用关系';

CREATE TABLE IF NOT EXISTS evidence_layers (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  window_id BIGINT UNSIGNED NOT NULL,
  code CHAR(6) NOT NULL,
  rank_no INT NOT NULL DEFAULT 0,
  market_evidence TEXT NULL,
  sector_evidence TEXT NULL,
  community_status ENUM('missing','weak','medium','strong') NOT NULL DEFAULT 'missing',
  community_main_claim VARCHAR(512) NOT NULL DEFAULT '',
  official_status VARCHAR(255) NOT NULL DEFAULT '',
  company_positioning TEXT NULL,
  hard_evidence_summary TEXT NULL,
  evidence_strength ENUM('pending','weak','medium','strong') NOT NULL DEFAULT 'pending' COMMENT 'pending=待补证据, weak=弱证据, medium=中等证据, strong=强证据',
  evidence_gaps TEXT NULL,
  next_evidence_action VARCHAR(512) NOT NULL DEFAULT '',
  why_hypothesis TEXT NULL,
  raw_json JSON NULL,
  built_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_evidence_layers_window_code (window_id, code),
  KEY idx_evidence_layers_strength (evidence_strength),
  KEY idx_evidence_layers_rank (window_id, rank_no),
  CONSTRAINT fk_evidence_layers_window
    FOREIGN KEY (window_id) REFERENCES windows(id)
    ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='综合证据层，回答为什么涨';

CREATE TABLE IF NOT EXISTS generated_posts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  window_id BIGINT UNSIGNED NOT NULL,
  code CHAR(6) NOT NULL,
  post_type ENUM('dav_info_gap','key_points','internal_summary','other') NOT NULL DEFAULT 'dav_info_gap',
  title VARCHAR(255) NOT NULL DEFAULT '',
  hook VARCHAR(512) NOT NULL DEFAULT '',
  content TEXT NOT NULL,
  publish_level ENUM('skip','watch','publish') NOT NULL DEFAULT 'watch',
  has_content TINYINT NOT NULL DEFAULT 1 COMMENT '大V版只保留有内容文案',
  raw_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_generated_posts_window_code_type (window_id, code, post_type),
  KEY idx_generated_posts_type_time (post_type, created_at),
  KEY idx_generated_posts_publish (publish_level, has_content),
  CONSTRAINT fk_generated_posts_window
    FOREIGN KEY (window_id) REFERENCES windows(id)
    ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='最终文案产物';

CREATE TABLE IF NOT EXISTS market_news_items (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  source ENUM('cls','wallstreetcn','other') NOT NULL DEFAULT 'other' COMMENT '资讯来源：财联社/华尔街见闻',
  source_item_id VARCHAR(128) NOT NULL DEFAULT '',
  item_kind ENUM('headline','important','red','live','other') NOT NULL DEFAULT 'other',
  published_at DATETIME(3) NULL,
  title VARCHAR(512) NOT NULL DEFAULT '',
  content MEDIUMTEXT NULL,
  url VARCHAR(1024) NOT NULL DEFAULT '',
  tags JSON NULL,
  importance TINYINT NOT NULL DEFAULT 0 COMMENT '0未知，1普通，2重要，3头条/强重要',
  source_status VARCHAR(255) NOT NULL DEFAULT '',
  raw_json JSON NULL,
  collected_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_market_news_source_item (source, source_item_id),
  KEY idx_market_news_time (published_at),
  KEY idx_market_news_kind_time (item_kind, published_at),
  KEY idx_market_news_importance_time (importance, published_at)
) ENGINE=InnoDB COMMENT='每日盘前市场资讯：财联社、华尔街见闻头条与重要快讯';

CREATE TABLE IF NOT EXISTS daily_market_themes (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  trade_date DATE NOT NULL COMMENT '交易日/观察日',
  theme_name VARCHAR(128) NOT NULL DEFAULT '',
  keywords JSON NULL COMMENT '命中关键词',
  source_count INT NOT NULL DEFAULT 0 COMMENT '命中资讯条数',
  source_titles JSON NULL COMMENT '代表性资讯标题',
  source_item_ids JSON NULL COMMENT 'market_news_items source/source_item_id 引用',
  related_industries JSON NULL COMMENT '可映射行业',
  related_concepts JSON NULL COMMENT '可映射概念',
  importance_score DECIMAL(10,2) NOT NULL DEFAULT 0 COMMENT '主题强度分',
  summary TEXT NULL COMMENT '一句话解释',
  raw_json JSON NULL,
  generated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_daily_market_themes_date_name (trade_date, theme_name),
  KEY idx_daily_market_themes_date_score (trade_date, importance_score),
  KEY idx_daily_market_themes_generated (generated_at)
) ENGINE=InnoDB COMMENT='每日盘前催化主题，由市场资讯加工生成';

CREATE TABLE IF NOT EXISTS ths_market_after_close_summaries (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  trade_date DATE NOT NULL,
  source VARCHAR(64) NOT NULL DEFAULT 'ths_after_close_summary',
  source_item_id VARCHAR(128) NOT NULL DEFAULT '',
  title VARCHAR(512) NOT NULL DEFAULT '',
  summary TEXT NULL,
  content MEDIUMTEXT NULL,
  url VARCHAR(1024) NOT NULL DEFAULT '',
  published_at DATETIME(3) NULL,
  source_status VARCHAR(255) NOT NULL DEFAULT '',
  raw_json JSON NULL,
  collected_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_ths_market_after_close_item (trade_date, source_item_id),
  KEY idx_ths_market_after_close_date (trade_date, published_at),
  KEY idx_ths_market_after_close_collected (collected_at)
) ENGINE=InnoDB COMMENT='THS after-close market review summary';

CREATE TABLE IF NOT EXISTS ths_hot_concept_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  trade_date DATE NOT NULL,
  event_id VARCHAR(64) NOT NULL DEFAULT '',
  title VARCHAR(512) NOT NULL DEFAULT '',
  investment_direction VARCHAR(128) NOT NULL DEFAULT '',
  heat BIGINT NOT NULL DEFAULT 0,
  create_ts BIGINT NULL,
  create_time DATETIME(3) NULL,
  has_topped TINYINT NULL,
  summary TEXT NULL,
  summary_items JSON NULL,
  jump_url VARCHAR(1024) NOT NULL DEFAULT '',
  themes_json JSON NULL,
  top_stocks_json JSON NULL,
  raw_json JSON NULL,
  collected_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_ths_hot_concept_event (event_id),
  KEY idx_ths_hot_concept_date_heat (trade_date, heat),
  KEY idx_ths_hot_concept_direction_date (investment_direction, trade_date)
) ENGINE=InnoDB COMMENT='同花顺今天炒什么事件列表';

CREATE TABLE IF NOT EXISTS ths_hot_concept_members (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  trade_date DATE NOT NULL,
  event_id VARCHAR(64) NOT NULL DEFAULT '',
  theme_id VARCHAR(64) NOT NULL DEFAULT '',
  theme_name VARCHAR(128) NOT NULL DEFAULT '',
  theme_type VARCHAR(64) NOT NULL DEFAULT '',
  index_code VARCHAR(32) NOT NULL DEFAULT '',
  index_name VARCHAR(128) NOT NULL DEFAULT '',
  market_id VARCHAR(32) NOT NULL DEFAULT '',
  stock_code CHAR(6) NOT NULL DEFAULT '',
  stock_name VARCHAR(64) NOT NULL DEFAULT '',
  stock_market_id VARCHAR(32) NOT NULL DEFAULT '',
  rise_percent DECIMAL(12,4) NULL,
  limit_up_state TINYINT NULL,
  reason VARCHAR(1024) NOT NULL DEFAULT '',
  raw_json JSON NULL,
  collected_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_ths_hot_member_event_theme_stock (event_id, theme_id, stock_code),
  KEY idx_ths_hot_member_date_stock (trade_date, stock_code),
  KEY idx_ths_hot_member_theme_date (theme_name, trade_date),
  KEY idx_ths_hot_member_event (event_id)
) ENGINE=InnoDB COMMENT='同花顺今天炒什么主题成分股';

CREATE TABLE IF NOT EXISTS ths_homepage_headline_themes (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  trade_date DATE NOT NULL,
  snapshot_id VARCHAR(32) NOT NULL,
  rank_no INT NOT NULL DEFAULT 0,
  theme_id VARCHAR(64) NOT NULL DEFAULT '',
  theme_name VARCHAR(128) NOT NULL DEFAULT '',
  theme_url VARCHAR(1024) NOT NULL DEFAULT '',
  index_code VARCHAR(32) NOT NULL DEFAULT '',
  block_name VARCHAR(128) NOT NULL DEFAULT '',
  block_gain DECIMAL(12,4) NULL,
  source VARCHAR(64) NOT NULL DEFAULT 'ths_homepage_headline',
  page_url VARCHAR(1024) NOT NULL DEFAULT '',
  raw_json JSON NULL,
  collected_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_ths_home_headline_snapshot_rank (snapshot_id, rank_no),
  UNIQUE KEY uk_ths_home_headline_date_theme (trade_date, source, theme_name),
  KEY idx_ths_home_headline_date_rank (trade_date, rank_no),
  KEY idx_ths_home_headline_collected (collected_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='THS homepage headline topic row themes.';

CREATE TABLE IF NOT EXISTS ths_homepage_headline_theme_members (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  trade_date DATE NOT NULL,
  snapshot_id VARCHAR(32) NOT NULL,
  theme_rank INT NOT NULL DEFAULT 0,
  theme_id VARCHAR(64) NOT NULL DEFAULT '',
  theme_name VARCHAR(128) NOT NULL DEFAULT '',
  index_code VARCHAR(32) NOT NULL DEFAULT '',
  block_name VARCHAR(128) NOT NULL DEFAULT '',
  stock_rank INT NOT NULL DEFAULT 0,
  stock_code CHAR(6) NOT NULL DEFAULT '',
  stock_name VARCHAR(64) NOT NULL DEFAULT '',
  stock_market_id VARCHAR(32) NOT NULL DEFAULT '',
  gain DECIMAL(12,4) NULL,
  source VARCHAR(64) NOT NULL DEFAULT 'ths_homepage_headline',
  raw_json JSON NULL,
  collected_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_ths_home_member_snapshot_theme_stock (snapshot_id, theme_id, stock_code),
  KEY idx_ths_home_member_date_theme (trade_date, theme_name, stock_rank),
  KEY idx_ths_home_member_date_stock (trade_date, stock_code),
  KEY idx_ths_home_member_index (index_code, stock_rank)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='Hot component stocks shown under THS homepage headline themes.';

CREATE TABLE IF NOT EXISTS limit_up_pool_items (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  trade_date DATE NOT NULL,
  code CHAR(6) NOT NULL,
  stock_name VARCHAR(64) NOT NULL DEFAULT '',
  pool_type VARCHAR(32) NOT NULL DEFAULT 'limit_up',
  status VARCHAR(32) NOT NULL DEFAULT 'limit_up',
  pct_change DECIMAL(10,4) NULL,
  latest_price DECIMAL(12,4) NULL,
  turnover_amount DECIMAL(20,2) NULL,
  float_market_value DECIMAL(20,2) NULL,
  total_market_value DECIMAL(20,2) NULL,
  turnover_rate DECIMAL(12,4) NULL,
  seal_amount DECIMAL(20,2) NULL,
  first_limit_time VARCHAR(32) NOT NULL DEFAULT '',
  last_limit_time VARCHAR(32) NOT NULL DEFAULT '',
  open_count INT NOT NULL DEFAULT 0,
  limit_up_stat VARCHAR(32) NOT NULL DEFAULT '',
  limit_up_days INT NOT NULL DEFAULT 0,
  industry_name VARCHAR(128) NOT NULL DEFAULT '',
  source VARCHAR(64) NOT NULL DEFAULT 'eastmoney_akshare_stock_zt_pool_em',
  raw_json JSON NULL,
  collected_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_limit_up_pool_day_source_code (trade_date, source, pool_type, code),
  KEY idx_limit_up_pool_day_time (trade_date, first_limit_time),
  KEY idx_limit_up_pool_code_day (code, trade_date),
  KEY idx_limit_up_pool_day_status (trade_date, status),
  KEY idx_limit_up_pool_day_industry (trade_date, industry_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='Limit-up pool items from market data providers.';

CREATE TABLE IF NOT EXISTS ths_stock_concept_explanations (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  code CHAR(6) NOT NULL,
  stock_name VARCHAR(64) NOT NULL DEFAULT '',
  market_id VARCHAR(32) NOT NULL DEFAULT '',
  concept_name VARCHAR(128) NOT NULL DEFAULT '',
  concept_id VARCHAR(64) NOT NULL DEFAULT '',
  quote_code VARCHAR(32) NOT NULL DEFAULT '',
  concept_market_id VARCHAR(32) NOT NULL DEFAULT '',
  fit_rank INT NOT NULL DEFAULT 0,
  tags JSON NULL,
  reason_explain VARCHAR(2048) NOT NULL DEFAULT '',
  sub_concepts_json JSON NULL,
  self_sub_reasons_json JSON NULL,
  leading_json JSON NULL,
  raw_json JSON NULL,
  fetched_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_ths_stock_concept_code_name (code, concept_name),
  KEY idx_ths_stock_concept_code_rank (code, fit_rank),
  KEY idx_ths_stock_concept_name (concept_name),
  KEY idx_ths_stock_concept_quote (quote_code)
) ENGINE=InnoDB COMMENT='THS stock concept page explanations: why a stock belongs to a concept.';

CREATE TABLE IF NOT EXISTS active_market_anchors (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  anchor_name VARCHAR(128) NOT NULL DEFAULT '',
  anchor_type VARCHAR(64) NOT NULL DEFAULT 'hot_concept',
  source VARCHAR(64) NOT NULL DEFAULT 'ths_hot_concept',
  first_seen_date DATE NULL,
  last_seen_date DATE NULL,
  active_days_14d INT NOT NULL DEFAULT 0,
  event_count_14d INT NOT NULL DEFAULT 0,
  total_heat_14d BIGINT NOT NULL DEFAULT 0,
  today_heat BIGINT NOT NULL DEFAULT 0,
  today_event_count INT NOT NULL DEFAULT 0,
  member_count_14d INT NOT NULL DEFAULT 0,
  today_member_count INT NOT NULL DEFAULT 0,
  limit_up_count_14d INT NOT NULL DEFAULT 0,
  today_limit_up_count INT NOT NULL DEFAULT 0,
  leader_codes JSON NULL,
  member_codes JSON NULL,
  keywords JSON NULL,
  related_themes JSON NULL,
  related_titles JSON NULL,
  final_score DECIMAL(14,4) NOT NULL DEFAULT 0,
  status ENUM('active','watch','cooling','expired') NOT NULL DEFAULT 'watch',
  raw_json JSON NULL,
  generated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_active_anchor_source_name (source, anchor_name),
  KEY idx_active_anchor_status_score (status, final_score),
  KEY idx_active_anchor_last_seen (last_seen_date),
  KEY idx_active_anchor_name (anchor_name)

) ENGINE=InnoDB COMMENT='近2周市场有效锚点池';

CREATE TABLE IF NOT EXISTS active_market_anchor_members (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  anchor_name VARCHAR(128) NOT NULL DEFAULT '',
  anchor_type VARCHAR(64) NOT NULL DEFAULT 'hot_concept',
  source VARCHAR(64) NOT NULL DEFAULT 'ths_hot_concept',
  code CHAR(6) NOT NULL DEFAULT '',
  stock_name VARCHAR(64) NOT NULL DEFAULT '',
  first_seen_date DATE NULL,
  last_seen_date DATE NULL,
  active_days_14d INT NOT NULL DEFAULT 0,
  event_count_14d INT NOT NULL DEFAULT 0,
  total_heat_14d BIGINT NOT NULL DEFAULT 0,
  limit_up_count_14d INT NOT NULL DEFAULT 0,
  theme_names JSON NULL,
  reasons JSON NULL,
  latest_reason VARCHAR(1024) NOT NULL DEFAULT '',
  confidence DECIMAL(12,4) NOT NULL DEFAULT 0,
  status ENUM('active','watch','cooling','expired') NOT NULL DEFAULT 'watch',
  raw_json JSON NULL,
  generated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_active_anchor_member (source, anchor_name, code),
  KEY idx_active_anchor_member_code_status (code, status, confidence),
  KEY idx_active_anchor_member_anchor (anchor_name, status, confidence),
  KEY idx_active_anchor_member_last_seen (last_seen_date)
) ENGINE=InnoDB COMMENT='Active market anchor to stock mapping for realtime joins.';

CREATE TABLE IF NOT EXISTS active_market_anchor_relations (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  anchor_name VARCHAR(128) NOT NULL DEFAULT '',
  anchor_type VARCHAR(64) NOT NULL DEFAULT 'hot_concept',
  source VARCHAR(64) NOT NULL DEFAULT 'ths_hot_concept',
  relation_type ENUM('anchor','theme','concept','keyword','industry','sub_industry') NOT NULL DEFAULT 'keyword',
  relation_name VARCHAR(128) NOT NULL DEFAULT '',
  confidence DECIMAL(12,4) NOT NULL DEFAULT 0,
  evidence_count INT NOT NULL DEFAULT 0,
  status ENUM('active','watch','cooling','expired') NOT NULL DEFAULT 'watch',
  raw_json JSON NULL,
  generated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_active_anchor_relation (source, anchor_name, relation_type, relation_name),
  KEY idx_active_anchor_relation_name (relation_type, relation_name, status, confidence),
  KEY idx_active_anchor_relation_anchor (anchor_name, status, confidence)
) ENGINE=InnoDB COMMENT='Maps expanded themes, concepts, keywords and industries to active market anchors.';

CREATE TABLE IF NOT EXISTS active_anchor_match_candidates (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  anchor_name VARCHAR(128) NOT NULL DEFAULT '',
  anchor_type VARCHAR(64) NOT NULL DEFAULT 'hot_concept',
  source VARCHAR(64) NOT NULL DEFAULT 'ths_hot_concept',
  code CHAR(6) NOT NULL DEFAULT '',
  stock_name VARCHAR(64) NOT NULL DEFAULT '',
  match_source VARCHAR(64) NOT NULL DEFAULT '',
  match_level ENUM('strong','medium','weak','fallback') NOT NULL DEFAULT 'medium',
  matched_term VARCHAR(128) NOT NULL DEFAULT '',
  evidence_text VARCHAR(1024) NOT NULL DEFAULT '',
  confidence DECIMAL(12,4) NOT NULL DEFAULT 0,
  status ENUM('active','watch','cooling','expired') NOT NULL DEFAULT 'watch',
  raw_json JSON NULL,
  generated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_active_anchor_match (source, anchor_name, code, match_source, matched_term),
  KEY idx_active_anchor_match_code (code, status, match_level, confidence),
  KEY idx_active_anchor_match_anchor (anchor_name, status, match_level, confidence),
  KEY idx_active_anchor_match_source (match_source, match_level)
) ENGINE=InnoDB COMMENT='Expanded stock to active anchor candidates used by realtime scanner.';

CREATE TABLE IF NOT EXISTS anchor_realtime_role_snapshots (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  snapshot_run_id VARCHAR(32) NOT NULL DEFAULT '',
  captured_at DATETIME(3) NOT NULL,
  anchor_name VARCHAR(128) NOT NULL DEFAULT '',
  anchor_type VARCHAR(64) NOT NULL DEFAULT 'hot_concept',
  source VARCHAR(64) NOT NULL DEFAULT 'ths_hot_concept',
  member_count INT NOT NULL DEFAULT 0,
  strong_count INT NOT NULL DEFAULT 0,
  medium_count INT NOT NULL DEFAULT 0,
  leader_code CHAR(6) NOT NULL DEFAULT '',
  leader_name VARCHAR(64) NOT NULL DEFAULT '',
  leader_score DECIMAL(14,4) NOT NULL DEFAULT 0,
  core_code CHAR(6) NOT NULL DEFAULT '',
  core_name VARCHAR(64) NOT NULL DEFAULT '',
  core_score DECIMAL(14,4) NOT NULL DEFAULT 0,
  total_amount DECIMAL(20,2) NOT NULL DEFAULT 0,
  avg_pct_change DECIMAL(10,4) NOT NULL DEFAULT 0,
  max_pct_change DECIMAL(10,4) NOT NULL DEFAULT 0,
  active_member_count INT NOT NULL DEFAULT 0,
  status ENUM('active','watch','cooling','expired') NOT NULL DEFAULT 'watch',
  raw_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_anchor_role_snapshot (snapshot_run_id, anchor_name),
  KEY idx_anchor_role_snapshot_anchor_time (anchor_name, captured_at),
  KEY idx_anchor_role_snapshot_time (captured_at),
  KEY idx_anchor_role_snapshot_status (status, captured_at)
) ENGINE=InnoDB COMMENT='Realtime leader/core snapshots computed inside active theme anchor stock pools.';

CREATE TABLE IF NOT EXISTS anchor_realtime_role_members (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  snapshot_run_id VARCHAR(32) NOT NULL DEFAULT '',
  captured_at DATETIME(3) NOT NULL,
  anchor_name VARCHAR(128) NOT NULL DEFAULT '',
  anchor_type VARCHAR(64) NOT NULL DEFAULT 'hot_concept',
  code CHAR(6) NOT NULL DEFAULT '',
  stock_name VARCHAR(64) NOT NULL DEFAULT '',
  match_level ENUM('strong','medium','weak','fallback') NOT NULL DEFAULT 'medium',
  match_source VARCHAR(64) NOT NULL DEFAULT '',
  matched_term VARCHAR(128) NOT NULL DEFAULT '',
  confidence DECIMAL(12,4) NOT NULL DEFAULT 0,
  pct_change DECIMAL(10,4) NULL,
  speed DECIMAL(10,4) NULL,
  amount DECIMAL(20,2) NULL,
  volume BIGINT NULL,
  price DECIMAL(12,4) NULL,
  leader_score DECIMAL(14,4) NOT NULL DEFAULT 0,
  core_score DECIMAL(14,4) NOT NULL DEFAULT 0,
  rank_leader INT NOT NULL DEFAULT 0,
  rank_core INT NOT NULL DEFAULT 0,
  role_label VARCHAR(64) NOT NULL DEFAULT '锚点成员',
  raw_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_anchor_role_member (snapshot_run_id, anchor_name, code),
  KEY idx_anchor_role_member_code_time (code, captured_at),
  KEY idx_anchor_role_member_anchor_role (anchor_name, role_label, captured_at),
  KEY idx_anchor_role_member_rank (snapshot_run_id, anchor_name, rank_leader, rank_core)
) ENGINE=InnoDB COMMENT='Realtime per-stock scoring inside active theme anchor pools.';

CREATE TABLE IF NOT EXISTS auction_candidates (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  trade_date DATE NOT NULL,
  captured_at DATETIME(3) NOT NULL,
  rank_no INT NOT NULL DEFAULT 0,
  code CHAR(6) NOT NULL,
  stock_name VARCHAR(64) NOT NULL DEFAULT '',
  auction_price DECIMAL(12,4) NULL,
  preclose DECIMAL(12,4) NULL,
  auction_pct DECIMAL(10,4) NULL COMMENT '竞价高开幅度',
  auction_amount DECIMAL(20,2) NULL COMMENT '竞价成交额',
  matched_volume BIGINT NULL,
  buy_pressure DECIMAL(10,4) NULL,
  industry VARCHAR(128) NOT NULL DEFAULT '',
  sub_industry VARCHAR(128) NOT NULL DEFAULT '',
  concepts JSON NULL,
  theme_matches JSON NULL,
  theme_score DECIMAL(10,2) NOT NULL DEFAULT 0,
  sector_hot_count INT NOT NULL DEFAULT 0,
  concept_hot_count INT NOT NULL DEFAULT 0,
  resonance_score DECIMAL(10,2) NOT NULL DEFAULT 0,
  score DECIMAL(10,2) NOT NULL DEFAULT 0,
  risk_flags VARCHAR(512) NOT NULL DEFAULT '',
  raw_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_auction_candidates_date_code (trade_date, code),
  KEY idx_auction_candidates_date_rank (trade_date, rank_no),
  KEY idx_auction_candidates_date_score (trade_date, score),
  KEY idx_auction_candidates_code_date (code, trade_date)
) ENGINE=InnoDB COMMENT='09:25竞价候选池：高开幅度、竞价金额、主题命中、板块共振';

CREATE TABLE IF NOT EXISTS auction_minute_analysis (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  trade_date DATE NOT NULL,
  snapshot_minute DATETIME(3) NOT NULL COMMENT 'Normalized minute, e.g. 09:20:00',
  captured_at DATETIME(3) NOT NULL,
  analysis_kind ENUM('pct_top10','limit_up_order','limit_down_order') NOT NULL,
  rank_no INT NOT NULL DEFAULT 0,
  code CHAR(6) NOT NULL,
  stock_name VARCHAR(64) NOT NULL DEFAULT '',
  auction_price DECIMAL(12,4) NULL,
  preclose DECIMAL(12,4) NULL,
  auction_pct DECIMAL(10,4) NULL,
  auction_amount DECIMAL(20,2) NULL,
  matched_volume BIGINT NULL,
  bid1 DECIMAL(12,4) NULL,
  ask1 DECIMAL(12,4) NULL,
  bid_vol1 BIGINT NULL,
  ask_vol1 BIGINT NULL,
  limit_side ENUM('up','down','none') NOT NULL DEFAULT 'none',
  limit_price DECIMAL(12,4) NULL,
  seal_volume BIGINT NULL COMMENT 'TDX level-1 queue volume, usually lots',
  seal_amount DECIMAL(20,2) NULL COMMENT 'Estimated seal value, volume * 100 * price',
  buy_pressure DECIMAL(10,4) NULL,
  industry VARCHAR(128) NOT NULL DEFAULT '',
  sub_industry VARCHAR(128) NOT NULL DEFAULT '',
  concepts JSON NULL,
  theme_matches JSON NULL,
  theme_score DECIMAL(10,2) NOT NULL DEFAULT 0,
  sector_hot_count INT NOT NULL DEFAULT 0,
  concept_hot_count INT NOT NULL DEFAULT 0,
  resonance_score DECIMAL(10,2) NOT NULL DEFAULT 0,
  score DECIMAL(10,2) NOT NULL DEFAULT 0,
  risk_flags VARCHAR(512) NOT NULL DEFAULT '',
  raw_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_auction_minute_kind_code (trade_date, snapshot_minute, analysis_kind, code),
  KEY idx_auction_minute_kind_rank (trade_date, snapshot_minute, analysis_kind, rank_no),
  KEY idx_auction_minute_code (code, trade_date, snapshot_minute),
  KEY idx_auction_minute_score (trade_date, snapshot_minute, score)
) ENGINE=InnoDB COMMENT='Call auction minute radar: pct top10 and largest limit-up/down sealed orders.';

CREATE TABLE IF NOT EXISTS auction_trend_summary (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  trade_date DATE NOT NULL,
  code CHAR(6) NOT NULL,
  stock_name VARCHAR(64) NOT NULL DEFAULT '',
  first_seen_minute DATETIME(3) NULL,
  last_seen_minute DATETIME(3) NULL,
  minute_count INT NOT NULL DEFAULT 0,
  pct_top_count INT NOT NULL DEFAULT 0,
  limit_up_count INT NOT NULL DEFAULT 0,
  limit_down_count INT NOT NULL DEFAULT 0,
  best_pct_rank INT NOT NULL DEFAULT 0,
  final_candidate_rank INT NOT NULL DEFAULT 0,
  first_auction_pct DECIMAL(10,4) NULL,
  last_auction_pct DECIMAL(10,4) NULL,
  pct_delta DECIMAL(10,4) NULL,
  first_auction_amount DECIMAL(20,2) NULL,
  last_auction_amount DECIMAL(20,2) NULL,
  amount_delta DECIMAL(20,2) NULL,
  amount_growth_ratio DECIMAL(12,4) NULL,
  max_seal_amount DECIMAL(20,2) NULL,
  last_seal_amount DECIMAL(20,2) NULL,
  theme_score DECIMAL(10,2) NOT NULL DEFAULT 0,
  theme_matches JSON NULL,
  sector_hot_count INT NOT NULL DEFAULT 0,
  concept_hot_count INT NOT NULL DEFAULT 0,
  final_score DECIMAL(10,2) NOT NULL DEFAULT 0,
  trend_score DECIMAL(10,2) NOT NULL DEFAULT 0,
  trend_label VARCHAR(64) NOT NULL DEFAULT '',
  key_points JSON NULL,
  action_hint VARCHAR(255) NOT NULL DEFAULT '',
  raw_json JSON NULL,
  generated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_auction_trend_date_code (trade_date, code),
  KEY idx_auction_trend_date_score (trade_date, trend_score),
  KEY idx_auction_trend_date_rank (trade_date, final_candidate_rank),
  KEY idx_auction_trend_code_date (code, trade_date)
) ENGINE=InnoDB COMMENT='Call auction 09:20-09:25 trend summary for final pre-open judgement.';

CREATE TABLE IF NOT EXISTS pipeline_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  window_id BIGINT UNSIGNED NULL,
  event_type VARCHAR(64) NOT NULL,
  stage VARCHAR(64) NOT NULL DEFAULT '',
  status ENUM('ok','skipped','failed','running') NOT NULL DEFAULT 'ok',
  duration_ms INT NOT NULL DEFAULT 0,
  message VARCHAR(1024) NOT NULL DEFAULT '',
  payload_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  KEY idx_pipeline_events_window_time (window_id, created_at),
  KEY idx_pipeline_events_stage_time (stage, created_at),
  KEY idx_pipeline_events_status (status, created_at),
  CONSTRAINT fk_pipeline_events_window
    FOREIGN KEY (window_id) REFERENCES windows(id)
    ON DELETE SET NULL
) ENGINE=InnoDB COMMENT='流程耗时、状态、错误流水';

CREATE TABLE IF NOT EXISTS research_pool_snapshots (
  trade_date DATE NOT NULL,
  rule VARCHAR(64) NOT NULL DEFAULT 'recent_limit_up_or_5d_gain_top',
  limit_up_days INT NOT NULL DEFAULT 5,
  gain_period_days INT NOT NULL DEFAULT 5,
  gain_top INT NOT NULL DEFAULT 30,
  code_count INT NOT NULL DEFAULT 0,
  source_dates_json JSON NULL,
  params_json JSON NULL,
  source_hash CHAR(64) NOT NULL DEFAULT '',
  generated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (trade_date, rule, limit_up_days, gain_period_days, gain_top),
  KEY idx_research_pool_snapshots_generated (generated_at)
) ENGINE=InnoDB COMMENT='Materialized daily research-pool snapshot headers.';

CREATE TABLE IF NOT EXISTS research_pool_items (
  trade_date DATE NOT NULL,
  rule VARCHAR(64) NOT NULL DEFAULT 'recent_limit_up_or_5d_gain_top',
  limit_up_days INT NOT NULL DEFAULT 5,
  gain_period_days INT NOT NULL DEFAULT 5,
  gain_top INT NOT NULL DEFAULT 30,
  code CHAR(6) NOT NULL,
  stock_name VARCHAR(64) NOT NULL DEFAULT '',
  pool_rank INT NOT NULL DEFAULT 0,
  source_kind VARCHAR(32) NOT NULL DEFAULT '',
  source_priority INT NOT NULL DEFAULT 0,
  source_rank INT NOT NULL DEFAULT 0,
  source_label VARCHAR(255) NOT NULL DEFAULT '',
  source_trade_date DATE NULL,
  limit_up_day_count INT NOT NULL DEFAULT 0,
  rank_5d INT NULL,
  pct_5d DECIMAL(10,4) NULL,
  latest_pct DECIMAL(10,4) NULL,
  raw_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (trade_date, rule, limit_up_days, gain_period_days, gain_top, code),
  KEY idx_research_pool_items_day_rank (trade_date, rule, pool_rank),
  KEY idx_research_pool_items_code_day (code, trade_date),
  KEY idx_research_pool_items_source (trade_date, source_kind, source_rank)
) ENGINE=InnoDB COMMENT='Materialized daily research-pool members consumed by incremental collectors.';

CREATE TABLE IF NOT EXISTS research_pool_theme_members (
  trade_date DATE NOT NULL,
  code CHAR(6) NOT NULL,
  stock_name VARCHAR(64) NOT NULL DEFAULT '',
  pool_rank INT NOT NULL DEFAULT 0,
  pool_source_kind VARCHAR(32) NOT NULL DEFAULT '',
  concept_name VARCHAR(128) NOT NULL DEFAULT '',
  concept_id VARCHAR(64) NOT NULL DEFAULT '',
  reason_explain VARCHAR(2048) NOT NULL DEFAULT '',
  fit_rank INT NOT NULL DEFAULT 0,
  theme_name VARCHAR(128) NOT NULL DEFAULT '',
  theme_rank INT NOT NULL DEFAULT 999,
  is_headline_theme TINYINT NOT NULL DEFAULT 0,
  match_type VARCHAR(64) NOT NULL DEFAULT '',
  match_score DECIMAL(10,4) NOT NULL DEFAULT 0,
  source_table VARCHAR(64) NOT NULL DEFAULT 'ths_stock_concept_explanations',
  source_key VARCHAR(128) NOT NULL DEFAULT '',
  raw_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (trade_date, code, theme_name, concept_name),
  KEY idx_research_pool_theme_day_theme (trade_date, theme_name, is_headline_theme, match_score),
  KEY idx_research_pool_theme_day_code (trade_date, code, pool_rank),
  KEY idx_research_pool_theme_headline (trade_date, is_headline_theme, theme_rank)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='Research-pool stocks mapped to THS concept explanations and headline theme dimensions.';

CREATE TABLE IF NOT EXISTS leaderboard_snapshots (
  trade_date DATE NOT NULL,
  rule VARCHAR(64) NOT NULL DEFAULT 'recent_limit_up_or_5d_gain_top',
  limit_up_days INT NOT NULL DEFAULT 5,
  gain_period_days INT NOT NULL DEFAULT 5,
  gain_top INT NOT NULL DEFAULT 30,
  source VARCHAR(64) NOT NULL DEFAULT 'post_close_confirm',
  leader_count INT NOT NULL DEFAULT 0,
  scope_count INT NOT NULL DEFAULT 0,
  source_hash CHAR(64) NOT NULL DEFAULT '',
  payload_json JSON NOT NULL,
  generated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (trade_date, rule, limit_up_days, gain_period_days, gain_top, source),
  KEY idx_leaderboard_snapshots_generated (generated_at),
  KEY idx_leaderboard_snapshots_source (source, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='Post-close confirmed leaderboard payload snapshots.';

CREATE TABLE IF NOT EXISTS scheduled_tasks (
  task_id VARCHAR(64) NOT NULL,
  task_name VARCHAR(255) NOT NULL DEFAULT '',
  task_description TEXT NULL,
  task_kind VARCHAR(64) NOT NULL,
  task_type ENUM('hot','warm','cold','render','maintenance') NOT NULL DEFAULT 'maintenance',
  enabled TINYINT NOT NULL DEFAULT 1,
  schedule_type ENUM('interval','manual') NOT NULL DEFAULT 'interval',
  update_interval_seconds INT NOT NULL DEFAULT 60,
  priority INT NOT NULL DEFAULT 100,
  timeout_seconds INT NOT NULL DEFAULT 1800,
  max_attempts INT NOT NULL DEFAULT 2,
  next_run_after DATETIME(3) NULL,
  last_enqueued_at DATETIME(3) NULL,
  payload_template_json JSON NULL,
  dedupe_key_template VARCHAR(255) NOT NULL DEFAULT '',
  last_message VARCHAR(1024) NOT NULL DEFAULT '',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (task_id),
  KEY idx_scheduled_tasks_due (enabled, schedule_type, next_run_after, priority),
  KEY idx_scheduled_tasks_kind (task_kind, task_type)
) ENGINE=InnoDB COMMENT='Scheduler task definitions; scheduler only enqueues, never executes.';

CREATE TABLE IF NOT EXISTS task_queue (
  queue_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  task_id VARCHAR(64) NOT NULL,
  task_kind VARCHAR(64) NOT NULL,
  task_type ENUM('hot','warm','cold','render','maintenance') NOT NULL DEFAULT 'maintenance',
  priority INT NOT NULL DEFAULT 100,
  status ENUM('pending','running','done','failed','dead','cancelled') NOT NULL DEFAULT 'pending',
  payload_json JSON NULL,
  dedupe_key VARCHAR(255) NOT NULL DEFAULT '',
  not_before DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  locked_by VARCHAR(128) NOT NULL DEFAULT '',
  locked_until DATETIME(3) NULL,
  claim_token VARCHAR(64) NOT NULL DEFAULT '',
  attempt_count INT NOT NULL DEFAULT 0,
  max_attempts INT NOT NULL DEFAULT 2,
  timeout_seconds INT NOT NULL DEFAULT 1800,
  open_dedupe_key VARCHAR(255) GENERATED ALWAYS AS (
    CASE WHEN status IN ('pending','running') AND dedupe_key <> '' THEN dedupe_key ELSE NULL END
  ) STORED,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  started_at DATETIME(3) NULL,
  finished_at DATETIME(3) NULL,
  last_error MEDIUMTEXT NULL,
  PRIMARY KEY (queue_id),
  UNIQUE KEY uk_task_queue_dedupe_open (open_dedupe_key),
  KEY idx_task_queue_pick (status, task_type, not_before, priority, created_at),
  KEY idx_task_queue_lock (status, locked_until),
  KEY idx_task_queue_claim (claim_token),
  KEY idx_task_queue_task (task_id),
  CONSTRAINT fk_task_queue_task
    FOREIGN KEY (task_id) REFERENCES scheduled_tasks(task_id)
    ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='Executable queue items decoupled from schedule definitions.';

CREATE TABLE IF NOT EXISTS task_runs (
  run_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  queue_id BIGINT UNSIGNED NOT NULL,
  task_id VARCHAR(64) NOT NULL,
  task_kind VARCHAR(64) NOT NULL,
  task_type ENUM('hot','warm','cold','render','maintenance') NOT NULL DEFAULT 'maintenance',
  worker_id VARCHAR(128) NOT NULL DEFAULT '',
  started_at DATETIME(3) NOT NULL,
  finished_at DATETIME(3) NULL,
  status ENUM('running','ok','skipped','failed','timeout','dead') NOT NULL DEFAULT 'running',
  duration_ms INT NOT NULL DEFAULT 0,
  return_code INT NULL,
  output_tail MEDIUMTEXT NULL,
  error_text MEDIUMTEXT NULL,
  payload_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (run_id),
  KEY idx_task_runs_task_time (task_id, started_at),
  KEY idx_task_runs_status_time (status, started_at),
  KEY idx_task_runs_queue (queue_id),
  CONSTRAINT fk_task_runs_queue
    FOREIGN KEY (queue_id) REFERENCES task_queue(queue_id)
    ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='Execution history for queued tasks.';

CREATE TABLE IF NOT EXISTS scheduled_task_health_checks (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  check_date DATE NOT NULL,
  checked_at DATETIME(3) NOT NULL,
  task_id VARCHAR(64) NOT NULL,
  task_name VARCHAR(255) NOT NULL DEFAULT '',
  task_kind VARCHAR(64) NOT NULL DEFAULT '',
  task_type VARCHAR(32) NOT NULL DEFAULT '',
  enabled TINYINT NOT NULL DEFAULT 1,
  expected_at DATETIME(3) NULL,
  next_run_after DATETIME(3) NULL,
  last_enqueued_at DATETIME(3) NULL,
  last_started_at DATETIME(3) NULL,
  last_finished_at DATETIME(3) NULL,
  last_success_at DATETIME(3) NULL,
  last_status VARCHAR(32) NOT NULL DEFAULT '',
  queue_pending_count INT NOT NULL DEFAULT 0,
  queue_running_count INT NOT NULL DEFAULT 0,
  queue_dead_count INT NOT NULL DEFAULT 0,
  queue_done_count INT NOT NULL DEFAULT 0,
  health ENUM('ok','not_due','missed','failed','overdue','pending','disabled') NOT NULL DEFAULT 'ok',
  severity ENUM('info','warning','critical') NOT NULL DEFAULT 'info',
  issue_code VARCHAR(64) NOT NULL DEFAULT '',
  message VARCHAR(1024) NOT NULL DEFAULT '',
  detail_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uniq_task_health_check_date_task (check_date, task_id),
  KEY idx_task_health_checks_health (check_date, health, severity),
  KEY idx_task_health_checks_task (task_id, checked_at)
) ENGINE=InnoDB COMMENT='Daily scheduler health check results for missed, delayed, and failed tasks.';

CREATE TABLE IF NOT EXISTS worker_heartbeats (
  worker_id VARCHAR(128) NOT NULL,
  worker_type VARCHAR(32) NOT NULL DEFAULT '',
  hostname VARCHAR(128) NOT NULL DEFAULT '',
  pid INT NOT NULL DEFAULT 0,
  status ENUM('idle','running','stopping','dead') NOT NULL DEFAULT 'idle',
  current_queue_id BIGINT UNSIGNED NULL,
  heartbeat_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  started_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  meta_json JSON NULL,
  PRIMARY KEY (worker_id),
  KEY idx_worker_heartbeats_type_time (worker_type, heartbeat_at),
  KEY idx_worker_heartbeats_queue (current_queue_id)
) ENGINE=InnoDB COMMENT='Worker liveness and current assignment.';

CREATE OR REPLACE VIEW v_latest_window_movers AS
SELECT wm.*
FROM window_movers wm
JOIN windows w ON w.id = wm.window_id
JOIN (
  SELECT id
  FROM windows
  WHERE status = 'done'
  ORDER BY ended_at DESC
  LIMIT 1
) latest ON latest.id = w.id;

CREATE OR REPLACE VIEW v_latest_evidence_layers AS
SELECT el.*
FROM evidence_layers el
JOIN windows w ON w.id = el.window_id
JOIN (
  SELECT id
  FROM windows
  WHERE status = 'done'
  ORDER BY ended_at DESC
  LIMIT 1
) latest ON latest.id = w.id;

CREATE OR REPLACE VIEW v_latest_generated_posts AS
SELECT gp.*
FROM generated_posts gp
JOIN windows w ON w.id = gp.window_id
JOIN (
  SELECT id
  FROM windows
  WHERE status = 'done'
  ORDER BY ended_at DESC
  LIMIT 1
) latest ON latest.id = w.id
WHERE gp.has_content = 1;

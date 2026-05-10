# 通达信全市场异动监控

用途：每 60 秒通过通达信行情服务器抓取全市场 A 股行情，生成全市场行情表、涨速榜、涨幅榜，并补充行业/概念标签。

## 脚本

```text
../../scripts/tdx_mover_watcher.py
```

## 数据来源

```text
实时行情：通达信行情服务器
行业映射：G:\D盘迁移\Tools\tdx\T0002\hq_cache\tdxhy.cfg
行业名称：G:\D盘迁移\Tools\tdx\T0002\hq_cache\tdxzs.cfg / tdxzs3.cfg
概念标签：G:\D盘迁移\Tools\tdx\T0002\hq_cache\specgpsxzt.txt / infoharbor_block.dat
```

## 输出

```text
../../data/stock/tdx_full_market_latest.csv
../../data/stock/tdx_mover_speed_top10_latest.csv
../../data/stock/tdx_mover_speed_top10_history.csv
../../data/stock/tdx_mover_pct_top10_latest.csv
../../data/stock/tdx_mover_judgement_latest.csv
../../data/stock/tdx_mover_judgement_history.csv
../../data/stock/tdx_mover_last_snapshot.json
../../data/stock/tdx_mover_seen.json
../../data/stock/tdx_mover_meta.json
```

## 使用

单次测试：

```powershell
python scripts\tdx_mover_watcher.py --once --top 10
```

持续每 60 秒运行：

```powershell
python scripts\tdx_mover_watcher.py --interval 60 --top 10
```

后台启动：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_tdx_mover_watcher.ps1
```

停止：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_tdx_mover_watcher.ps1
```

## 说明

第一轮没有历史快照，`speed` 使用今日涨幅作为临时基准，`basis=pct_change_first_run`。

第二轮开始，`speed` 使用当前价和上一轮快照价计算：

```text
speed = 当前价 / 上一轮价格 - 1
```

如果市场休市或午间没有价格变化，涨速榜可能为空，这是正常现象。

现在的 `latest` 展示规则：

```text
交易中：按本轮结果刷新 latest
午休 / 收盘 / 非交易日：如果本轮没有有效涨速，则保留最后一组 speed > 0 的数据
```

运行状态可在 `tdx_mover_meta.json` 里查看：

```text
market_phase：trading / lunch_break / market_closed / non_trading_day
preserve_last_mover：是否保留最后有效异动
restored_speed_latest：是否从历史里恢复了涨速榜
```

## 异动判断

判断表：

```text
../../data/stock/tdx_mover_judgement_latest.csv
```

核心字段：

```text
candidate_basis：speed 表示来自涨速榜；pct_fallback_no_speed 表示当前无涨速，用涨幅榜回退
freshness：新上榜 / 重复出现 / 反复出现
speed_signal：急拉 / 明显拉升 / 轻微异动 / 暂无快照涨速
pct_position：初动观察 / 中段拉升 / 涨幅偏高 / 10cm涨停附近 / 20cm涨停附近
amount_confirm：成交偏弱 / 成交一般 / 成交有效 / 成交强确认
linkage_signal：个股孤立 / 有联动 / 板块扩散
action_bucket：观察池 / 等待验证 / 回避池
risk_flags：主要风险点
key_points：简要判断要点
```

# 股票异动证据层

## 当前原则

`evidence_layers` 是“为什么异动”的唯一结论表。它不再依赖旧 CSV 多阶段产物。

来源：

```text
window_movers
evidence_candidates
stock_company_profiles
community_evidence
official_evidence
```

输出：

```text
evidence_layers
generated_posts
```

## 重建证据层

```powershell
python scripts\build_stock_evidence_layer.py --mysql-enabled --mysql-user root --mysql-password <MYSQL_PASSWORD> --mysql-window-id 20260507_134934 --mysql-write-evidence-layer --no-file-output
```

## 生成最简文案

```powershell
python scripts\render_mysql_dav_info_gap_posts.py --mysql-enabled --mysql-user root --mysql-password <MYSQL_PASSWORD> --mysql-window-id 20260507_134934
```

文案格式固定为：

```text
股票代码 股票名
看点：一句话说明异动方向
证据：只放有内容的证据
待核：下一步核什么
风险：有风险才展示
```

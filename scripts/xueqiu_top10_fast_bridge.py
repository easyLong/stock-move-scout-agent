#!/usr/bin/env python
"""
Collect Xueqiu hot posts through a lightweight real-browser bridge.

Why this exists:
- Direct requests to Xueqiu APIs currently hit WAF/login checks.
- Full open-computer-use snapshots of each stock page are slow and huge.
- This script still reuses the logged-in Chrome session, but it runs a small
  JavaScript extractor inside the Xueqiu page and snapshots only compact JSON.
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import os
import re
import subprocess
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from stock_scout_mysql import add_mysql_args, mysql_config_from_args, window_evidence_candidate_rows


HOT_POST_COLUMNS = [
    "fetched_at",
    "rank_speed",
    "code",
    "name",
    "symbol",
    "hot_rank",
    "time_hint",
    "user",
    "title",
    "text",
    "detail_url",
    "repost_count",
    "comment_count",
    "like_count",
    "heat_score",
    "source_status",
    "snapshot_path",
]

EVIDENCE_COLUMNS = [
    "fetched_at",
    "rank_speed",
    "code",
    "name",
    "symbol",
    "comment_count",
    "hot_post_count",
    "hot_terms",
    "community_explanation",
    "evidence_value",
    "evidence_gap",
    "sample_comments",
    "sample_hot_posts",
    "source_status",
    "snapshot_path",
]

THEME_KEYWORDS = [
    "半导体",
    "芯片",
    "先进封装",
    "PCB",
    "RCC",
    "mSAP",
    "CoWoP",
    "CPO",
    "消费电子",
    "汽车电子",
    "新能源",
    "跨境电商",
    "算力",
    "租赁",
    "B300",
    "英伟达",
    "AI",
    "机器人",
    "商业航天",
    "火箭",
    "星曜宇航",
    "业绩",
    "订单",
    "涨价",
    "龙虎榜",
    "机构",
    "增持",
    "资金",
    "涨停",
]


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def market_symbol(code: str) -> str:
    return f"SH{code}" if code.startswith(("6", "9")) else f"SZ{code}"


def read_top(path: Path, limit: int) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))[:limit]


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_detail_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"detail_cache_read_failed={type(exc).__name__}:{exc}")
        return {}
    return data if isinstance(data, dict) else {}


def write_detail_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def post_id_value(post: dict[str, Any]) -> str:
    value = post.get("id", "")
    return str(value).strip() if value is not None else ""


def has_cached_detail(cache: dict[str, Any], post_id: str) -> bool:
    cached = cache.get(post_id)
    if not isinstance(cached, dict):
        return False
    return len(str(cached.get("text", "")).strip()) >= 8


def missing_detail_ids(data: dict[str, Any], cache: dict[str, Any], limit: int) -> tuple[list[str], int]:
    ids: list[str] = []
    hits = 0
    seen: set[str] = set()
    for post in data.get("posts", [])[:limit]:
        post_id = post_id_value(post)
        if not post_id or post_id in seen:
            continue
        seen.add(post_id)
        if has_cached_detail(cache, post_id):
            hits += 1
        else:
            ids.append(post_id)
    return ids, hits


def merge_api_data_with_detail_cache(data: dict[str, Any], cache: dict[str, Any]) -> dict[str, Any]:
    merged_data = dict(data)
    merged_posts: list[dict[str, Any]] = []
    for post in data.get("posts", []):
        if not isinstance(post, dict):
            continue
        post_id = post_id_value(post)
        cached = cache.get(post_id) if post_id else None
        if isinstance(cached, dict) and str(cached.get("text", "")).strip():
            merged = dict(cached)
            # Keep engagement counts fresh from the current list API.
            for key in (
                "created_at",
                "author",
                "followers",
                "title",
                "reply_count",
                "retweet_count",
                "like_count",
                "target",
            ):
                value = post.get(key)
                if value not in (None, ""):
                    merged[key] = value
            merged["id"] = post_id
            merged["text"] = str(cached.get("text", ""))
            merged["full_text"] = True
            merged["cache_hit"] = True
            merged["text_len"] = len(merged["text"])
            merged_posts.append(merged)
        else:
            merged_posts.append(dict(post))
    merged_data["posts"] = merged_posts
    return merged_data


def update_detail_cache(cache: dict[str, Any], symbol: str, posts: list[dict[str, Any]]) -> int:
    changed = 0
    for post in posts:
        if not isinstance(post, dict):
            continue
        post_id = post_id_value(post)
        text = re.sub(r"\s+", " ", str(post.get("text", ""))).strip()
        if not post_id or len(text) < 8 or not post.get("full_text"):
            continue
        record = {
            "id": post_id,
            "created_at": post.get("created_at", ""),
            "author": post.get("author", ""),
            "followers": post.get("followers", 0),
            "title": post.get("title", ""),
            "text": text,
            "reply_count": post.get("reply_count", 0),
            "retweet_count": post.get("retweet_count", 0),
            "like_count": post.get("like_count", 0),
            "target": post.get("target", ""),
            "symbol": symbol,
            "fetched_at": now_text(),
            "source": "xueqiu_status_show",
        }
        if cache.get(post_id) != record:
            cache[post_id] = record
            changed += 1
    return changed


def ocu_command(*args: str) -> list[str]:
    if os.name == "nt":
        return ["cmd", "/c", "open-computer-use", *args]
    return ["open-computer-use", *args]


def run_ocu_calls(calls_file: Path, timeout: int) -> str:
    env = os.environ.copy()
    env["OPEN_COMPUTER_USE_WINDOWS_ALLOW_UIA_TEXT_FALLBACK"] = "1"
    result = subprocess.run(
        ocu_command("call", "--calls-file", str(calls_file)),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stdout + "\n" + result.stderr).strip()[-2000:])
    return result.stdout


def run_snapshot(timeout: int) -> str:
    result = subprocess.run(
        ocu_command("snapshot", "chrome"),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stdout + "\n" + result.stderr).strip()[-2000:])
    return result.stdout


class VerificationRequired(RuntimeError):
    pass


def snapshot_has_verification(snapshot: str) -> bool:
    indicators = [
        "Access Verification",
        "aliyun_waf",
        "renderData",
        "滑动",
        "验证",
        "请完成",
        "安全验证",
    ]
    return any(indicator in snapshot for indicator in indicators)


def wait_for_manual_verification(symbol: str, wait_seconds: int, timeout: int) -> bool:
    if wait_seconds <= 0:
        return False
    print(f"{symbol}: need_manual_verify, please finish the slider in Chrome within {wait_seconds}s")
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        time.sleep(5)
        try:
            snapshot = run_snapshot(timeout)
        except Exception:
            continue
        if not snapshot_has_verification(snapshot):
            print(f"{symbol}: manual_verify_cleared")
            return True
    print(f"{symbol}: manual_verify_timeout")
    return False


def open_symbol(symbol: str, work_dir: Path, pause: float, timeout: int) -> None:
    calls = [
        {"tool": "get_app_state", "args": {"app": "chrome"}},
        {
            "tool": "set_value",
            "args": {
                "app": "chrome",
                "element_index": "17",
                "value": f"https://xueqiu.com/S/{symbol}",
            },
        },
        {"tool": "press_key", "args": {"app": "chrome", "key": "Enter"}},
    ]
    calls_file = work_dir / f"fast_open_{symbol}.json"
    calls_file.write_text(json.dumps(calls, ensure_ascii=False), encoding="utf-8")
    run_ocu_calls(calls_file, timeout)
    time.sleep(pause)


def legacy_extractor_js(post_limit: int) -> str:
    # Keep this compact because it is executed through the address bar.
    return (
        "javascript:(async()=>{try{"
        "let h=[...document.querySelectorAll('a')].find(a=>a.innerText.trim()==='热帖');"
        "if(h){h.click();await new Promise(r=>setTimeout(r,1200));}"
        f"let posts=[...document.querySelectorAll('article')].slice(0,{post_limit}).map((a,i)=>"
        "({rank:i+1,text:a.innerText,links:[...a.querySelectorAll('a')].map(x=>"
        "({text:x.innerText,href:x.href})).slice(0,24)}));"
        "document.body.innerText='XQ_DOM_JSON:'+JSON.stringify({url:location.href,title:document.title,count:posts.length,posts})"
        "}catch(e){document.body.innerText='XQ_DOM_ERROR:'+e}})()"
    )


def legacy_extract_dom_json(snapshot: str) -> dict[str, Any]:
    marker = "XQ_DOM_JSON:"
    start = snapshot.find(marker)
    if start < 0:
        err = snapshot.find("XQ_DOM_ERROR:")
        if err >= 0:
            raise RuntimeError(snapshot[err : err + 500])
        raise RuntimeError("XQ_DOM_JSON marker not found")
    start += len(marker)
    end = snapshot.find(" Secondary Actions:", start)
    if end < 0:
        end = snapshot.find("\n", start)
    if end < 0:
        end = len(snapshot)
    payload = snapshot[start:end].strip()
    return json.loads(payload)


def legacy_run_dom_extract(symbol: str, work_dir: Path, pause: float, timeout: int, post_limit: int) -> tuple[dict[str, Any], str]:
    js = extractor_js(post_limit)
    calls = [
        {"tool": "get_app_state", "args": {"app": "chrome"}},
        {"tool": "set_value", "args": {"app": "chrome", "element_index": "17", "value": js}},
        {"tool": "press_key", "args": {"app": "chrome", "key": "Enter"}},
    ]
    calls_file = work_dir / f"fast_extract_{symbol}.json"
    calls_file.write_text(json.dumps(calls, ensure_ascii=False), encoding="utf-8")
    run_ocu_calls(calls_file, timeout)
    time.sleep(pause)
    snapshot = run_snapshot(timeout)
    return extract_dom_json(snapshot), snapshot


def extractor_js(post_limit: int) -> str:
    # Split the marker so a failed address-bar execution is not parsed as data.
    return (
        "javascript:(async()=>{try{"
        "let m='XQ_DOM_'+'JSON:',em='XQ_DOM_'+'ERROR:';"
        "let pack=o=>btoa(unescape(encodeURIComponent(JSON.stringify(o))));"
        "let h=[...document.querySelectorAll('a,button')].find(a=>/(\\u70ed\\u5e16|\\u70ed\\u95e8)/.test(a.innerText.trim()));"
        "if(h){h.click();await new Promise(r=>setTimeout(r,1200));}"
        f"let posts=[...document.querySelectorAll('article')].slice(0,{post_limit}).map((a,i)=>"
        "({rank:i+1,text:a.innerText,links:[...a.querySelectorAll('a')].map(x=>"
        "({text:x.innerText,href:x.href})).slice(0,24)}));"
        "document.body.innerText=m+pack({url:location.href,title:document.title,count:posts.length,posts})"
        "}catch(e){document.body.innerText=em+e}})()"
    )


def decode_marker_payload(payload: str) -> dict[str, Any]:
    payload = payload.lstrip()
    if payload.startswith("{"):
        data, _ = json.JSONDecoder().raw_decode(payload)
        return data
    match = re.match(r"([A-Za-z0-9+/=]{16,})", payload)
    if not match:
        raise ValueError("marker payload is neither JSON nor base64")
    raw = base64.b64decode(match.group(1), validate=True)
    data = json.loads(raw.decode("utf-8"))
    return data


def extract_dom_json(snapshot: str) -> dict[str, Any]:
    marker = "XQ_DOM_JSON:"
    errors: list[str] = []
    for match in re.finditer(re.escape(marker), snapshot):
        payload = snapshot[match.end() :].lstrip()
        try:
            dom = decode_marker_payload(payload)
        except Exception as exc:
            errors.append(f"json:{exc}:{payload[:120].replace(chr(10), ' ')}")
            continue
        if isinstance(dom, dict) and isinstance(dom.get("posts"), list):
            return dom
        errors.append(f"unexpected_payload:{type(dom).__name__}")

    err = snapshot.find("XQ_DOM_ERROR:")
    if err >= 0:
        raise RuntimeError(snapshot[err : err + 500])
    if errors:
        raise RuntimeError("XQ_DOM_JSON marker found but no valid body JSON: " + " | ".join(errors[:3]))
    raise RuntimeError("XQ_DOM_JSON marker not found")


def snapshot_line_payload(line: str) -> str:
    compact = re.sub(r"\s+", " ", line).strip()
    match = re.match(r"^\d+\s+\S+\s*(.*?)\s*(?:Value:|Secondary Actions:|Frame:|$)", compact)
    if not match:
        return ""
    return match.group(1).strip()


def snapshot_line_href(line: str) -> str:
    match = re.search(r"Value:\s+(https://xueqiu\.com/\S+)", line)
    return match.group(1).split("#")[0] if match else ""


def dom_from_snapshot(snapshot: str, post_limit: int, stock: dict[str, str] | None = None) -> dict[str, Any]:
    lines = snapshot.splitlines()
    article_role = "\u6587\u7ae0"
    link_role = "\u94fe\u63a5"
    text_role = "\u6587\u672c"
    title_role = "\u6807\u9898"
    starts = [idx for idx, line in enumerate(lines) if re.search(rf"\s\d+\s+{article_role}\b", line)]
    posts: list[dict[str, Any]] = []
    symbol = market_symbol(stock["code"]) if stock and stock.get("code") else ""

    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else min(len(lines), start + 90)
        block = lines[start:end]
        block_text = "\n".join(block)
        if stock and stock.get("name") not in block_text and stock.get("code") not in block_text and symbol not in block_text:
            continue

        text_parts: list[str] = []
        links: list[dict[str, str]] = []
        for line in block:
            href = snapshot_line_href(line)
            payload = snapshot_line_payload(line)
            if href:
                links.append({"text": payload, "href": href})
            if any(role in line for role in (link_role, text_role, title_role)) and payload:
                if payload not in {"", "\u8f6c\u53d1", "\u8ba8\u8bba", "\u8d5e", "\u6536\u85cf"}:
                    text_parts.append(payload)

        text = "\n".join(text_parts).strip()
        if len(text) < 8:
            continue
        posts.append({"rank": len(posts) + 1, "text": text, "links": links[:24]})
        if len(posts) >= post_limit:
            break

    return {"url": "", "title": "", "count": len(posts), "posts": posts}


def js_extract_calls(js: str, method: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = [
        {"tool": "get_app_state", "args": {"app": "chrome"}},
        {"tool": "press_key", "args": {"app": "chrome", "key": "Ctrl+L"}},
    ]
    if method == "type_text":
        calls.append({"tool": "type_text", "args": {"app": "chrome", "text": js}})
    else:
        calls.append({"tool": "set_value", "args": {"app": "chrome", "element_index": "17", "value": js}})
    calls.append({"tool": "press_key", "args": {"app": "chrome", "key": "Enter"}})
    return calls


def run_dom_extract(symbol: str, work_dir: Path, pause: float, timeout: int, post_limit: int) -> tuple[dict[str, Any], str]:
    js = extractor_js(post_limit)
    failures: list[str] = []
    last_snapshot = ""
    for method in ("set_value", "type_text"):
        calls_file = work_dir / f"fast_extract_{symbol}_{method}.json"
        calls_file.write_text(json.dumps(js_extract_calls(js, method), ensure_ascii=False), encoding="utf-8")
        try:
            run_ocu_calls(calls_file, timeout)
            time.sleep(pause)
            last_snapshot = run_snapshot(timeout)
            try:
                return extract_dom_json(last_snapshot), last_snapshot
            except Exception as exc:
                fallback_dom = dom_from_snapshot(last_snapshot, post_limit)
                if fallback_dom.get("posts"):
                    return fallback_dom, last_snapshot
                raise exc
        except Exception as exc:
            failures.append(f"{method}:{type(exc).__name__}:{exc}")
            time.sleep(0.6)
    raise RuntimeError("DOM extract failed after retries: " + " || ".join(failures) + f" snapshot_len={len(last_snapshot)}")


def api_fetch_js(symbol: str, post_limit: int, sort: str, fetch_detail: bool) -> str:
    count = max(post_limit, 20)
    detail_flag = "true" if fetch_detail else "false"
    return (
        "javascript:(async()=>{try{"
        f"let sym='{symbol}';"
        "let m='XQ_API_'+'JSON_'+sym+':',em='XQ_API_'+'ERROR_'+sym+':';"
        "let pack=o=>btoa(unescape(encodeURIComponent(JSON.stringify(o))));"
        "let clean=s=>{let d=document.createElement('div');d.innerHTML=s||'';return d.innerText.replace(/\\s+/g,' ').trim()};"
        f"let u='/query/v1/symbol/search/status.json?count={count}&comment=0&symbol={symbol}&hl=0&source=all&sort={sort}&page=1&_='+Date.now();"
        "let r=await fetch(u,{credentials:'include',headers:{'accept':'application/json, text/plain, */*','x-requested-with':'XMLHttpRequest'}});"
        "let d=await r.json();"
        f"let base=(d.list||[]).slice(0,{post_limit});"
        "let sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));"
        "let posts=[];"
        "for(let i=0;i<base.length;i++){let x=base[i],detail=null;"
        f"if({detail_flag}&&x.id){{try{{await sleep(650+Math.floor(Math.random()*450));let rr=await fetch('/statuses/show.json?id='+x.id,{{credentials:'include',headers:{{'accept':'application/json, text/plain, */*','x-requested-with':'XMLHttpRequest'}}}});if(rr.ok)detail=await rr.json();}}catch(_e){{}}}}"
        "let y=detail||x;"
        "posts.push({id:x.id,created_at:y.created_at||x.created_at,author:(x.user&&x.user.screen_name)||(y.user&&y.user.screen_name),followers:(x.user&&x.user.followers_count)||(y.user&&y.user.followers_count),"
        "title:y.title||x.title||'',text:clean(y.text||y.description||x.text||x.description||''),"
        "reply_count:y.reply_count||x.reply_count||0,retweet_count:y.retweet_count||x.retweet_count||0,like_count:y.like_count||x.like_count||0,target:y.target||x.target||'',"
        "full_text:!!(detail&&detail.text),text_len:clean(y.text||y.description||x.text||x.description||'').length});"
        "}"
        "document.body.innerText=m+pack({url:location.href,title:document.title,status:r.status,ct:r.headers.get('content-type'),about:d.about,count:d.count,posts})"
        "}catch(e){document.body.innerText=em+(e&&e.stack?e.stack:e)}})()"
    )


def api_detail_fetch_js(symbol: str, detail_ids: list[str]) -> str:
    ids_json = json.dumps(detail_ids, ensure_ascii=False)
    return (
        "javascript:(async()=>{try{"
        f"let sym='{symbol}',ids={ids_json};"
        "let m='XQ_API_'+'JSON_'+sym+':',em='XQ_API_'+'ERROR_'+sym+':';"
        "let pack=o=>btoa(unescape(encodeURIComponent(JSON.stringify(o))));"
        "let clean=s=>{let d=document.createElement('div');d.innerHTML=s||'';return d.innerText.replace(/\\s+/g,' ').trim()};"
        "let sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));"
        "let posts=[];"
        "for(let i=0;i<ids.length;i++){let id=ids[i],y=null;"
        "try{await sleep(650+Math.floor(Math.random()*450));let rr=await fetch('/statuses/show.json?id='+encodeURIComponent(id),{credentials:'include',headers:{'accept':'application/json, text/plain, */*','x-requested-with':'XMLHttpRequest'}});if(rr.ok)y=await rr.json();else posts.push({id,status:rr.status,error:'detail_http_'+rr.status});}catch(e){posts.push({id,error:String(e&&e.message?e.message:e)});}"
        "if(y){let text=clean(y.text||y.description||'');posts.push({id:y.id||id,created_at:y.created_at,author:(y.user&&y.user.screen_name)||'',followers:(y.user&&y.user.followers_count)||0,"
        "title:y.title||'',text:text,reply_count:y.reply_count||0,retweet_count:y.retweet_count||0,like_count:y.like_count||0,target:y.target||'',full_text:!!y.text,text_len:text.length});}"
        "}"
        "document.body.innerText=m+pack({url:location.href,title:document.title,status:200,about:sym,count:posts.length,posts,detail_ids:ids})"
        "}catch(e){document.body.innerText=em+(e&&e.stack?e.stack:e)}})()"
    )


def extract_api_json(snapshot: str, symbol: str) -> dict[str, Any]:
    marker = f"XQ_API_JSON_{symbol}:"
    errors: list[str] = []
    for match in re.finditer(re.escape(marker), snapshot):
        payload = snapshot[match.end() :].lstrip()
        try:
            data = decode_marker_payload(payload)
        except Exception as exc:
            errors.append(f"json:{exc}:{payload[:120].replace(chr(10), ' ')}")
            continue
        if isinstance(data, dict) and isinstance(data.get("posts"), list):
            return data
        errors.append(f"unexpected_payload:{type(data).__name__}")

    err = snapshot.find(f"XQ_API_ERROR_{symbol}:")
    if err >= 0:
        raise RuntimeError(snapshot[err : err + 500])
    if errors:
        raise RuntimeError(f"XQ_API_JSON marker found for {symbol} but no valid body JSON: " + " | ".join(errors[:3]))
    raise RuntimeError(f"XQ_API_JSON marker not found for {symbol}")


def run_api_fetch(
    symbol: str,
    work_dir: Path,
    pause: float,
    timeout: int,
    post_limit: int,
    sort: str,
    fetch_detail: bool,
    manual_verify_wait: int,
    verify_retries: int,
) -> tuple[dict[str, Any], str]:
    js = api_fetch_js(symbol, post_limit, sort, fetch_detail)
    failures: list[str] = []
    last_snapshot = ""
    for method in ("set_value", "type_text"):
        attempts = 0
        while attempts <= verify_retries:
            calls_file = work_dir / f"api_fetch_{symbol}_{method}_{attempts}.json"
            calls_file.write_text(json.dumps(js_extract_calls(js, method), ensure_ascii=False), encoding="utf-8")
            try:
                run_ocu_calls(calls_file, timeout)
                time.sleep(max(1.0, pause / 2))
                last_snapshot = run_snapshot(timeout)
                if snapshot_has_verification(last_snapshot):
                    if attempts < verify_retries and wait_for_manual_verification(symbol, manual_verify_wait, timeout):
                        attempts += 1
                        continue
                    raise VerificationRequired(f"{symbol} requires manual slider verification")
                return extract_api_json(last_snapshot, symbol), last_snapshot
            except VerificationRequired:
                raise
            except Exception as exc:
                failures.append(f"{method}:{type(exc).__name__}:{exc}")
                time.sleep(0.6)
                break
    raise RuntimeError("API fetch failed after retries: " + " || ".join(failures) + f" snapshot_len={len(last_snapshot)}")


def run_api_detail_fetch(
    symbol: str,
    work_dir: Path,
    pause: float,
    timeout: int,
    detail_ids: list[str],
    manual_verify_wait: int,
    verify_retries: int,
) -> tuple[dict[str, Any], str]:
    if not detail_ids:
        return {"about": symbol, "count": 0, "posts": []}, ""
    js = api_detail_fetch_js(symbol, detail_ids)
    failures: list[str] = []
    last_snapshot = ""
    for method in ("set_value", "type_text"):
        attempts = 0
        while attempts <= verify_retries:
            calls_file = work_dir / f"api_detail_fetch_{symbol}_{method}_{attempts}.json"
            calls_file.write_text(json.dumps(js_extract_calls(js, method), ensure_ascii=False), encoding="utf-8")
            try:
                run_ocu_calls(calls_file, timeout)
                time.sleep(max(1.0, pause / 2))
                last_snapshot = run_snapshot(timeout)
                if snapshot_has_verification(last_snapshot):
                    if attempts < verify_retries and wait_for_manual_verification(symbol, manual_verify_wait, timeout):
                        attempts += 1
                        continue
                    raise VerificationRequired(f"{symbol} requires manual slider verification")
                return extract_api_json(last_snapshot, symbol), last_snapshot
            except VerificationRequired:
                raise
            except Exception as exc:
                failures.append(f"{method}:{type(exc).__name__}:{exc}")
                time.sleep(0.6)
                break
    raise RuntimeError("API detail fetch failed after retries: " + " || ".join(failures) + f" snapshot_len={len(last_snapshot)}")


def first_post_url(links: list[dict[str, str]]) -> str:
    for link in links:
        href = link.get("href", "")
        if re.search(r"https://xueqiu.com/\d+/\d+", href):
            return href.split("#")[0]
    return ""


def first_user(links: list[dict[str, str]]) -> str:
    for link in links:
        text = re.sub(r"\s+", " ", link.get("text", "")).strip()
        href = link.get("href", "")
        if text and re.fullmatch(r"https://xueqiu.com/\d+", href):
            return text
    return ""


def first_time(links: list[dict[str, str]]) -> str:
    for link in links:
        text = re.sub(r"\s+", " ", link.get("text", "")).strip()
        href = link.get("href", "")
        if text and re.search(r"https://xueqiu.com/\d+/\d+", href):
            return text
    return "页面可见"


def parse_metrics(text: str) -> tuple[str, str, str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    def after_icon(icon: str, label: str) -> str:
        for idx, line in enumerate(lines):
            if line.startswith(icon):
                tail = line.replace(icon, "").replace(label, "").strip()
                if tail.isdigit():
                    return tail
                if idx + 1 < len(lines) and lines[idx + 1].isdigit():
                    return lines[idx + 1]
                return "0"
            if line == label:
                return "0"
        return ""

    repost = after_icon("", "转发")
    comment = after_icon("", "讨论")
    like = after_icon("", "赞")
    return repost, comment, like


def to_int(value: str) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def clean_post_text(raw: str, stock: dict[str, str]) -> tuple[str, str]:
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    body: list[str] = []
    title = ""
    skip_next = False
    for idx, line in enumerate(lines):
        if idx == 0:
            continue
        if any(icon in line for icon in ["", "", "", ""]):
            break
        if line in {"转发", "讨论", "赞", "收藏", "收起", "展开"}:
            continue
        if skip_next:
            skip_next = False
            continue
        if not title and stock["name"] in line and len(line) >= 12:
            title = line
            continue
        body.append(line)
    text = re.sub(r"\s+", " ", " ".join(body)).strip()
    return title, text


def api_time_hint(value: Any) -> str:
    try:
        ts = int(value) / 1000
    except Exception:
        return "api_visible"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def normalize_api_posts(stock: dict[str, str], data: dict[str, Any], snapshot_path: Path, limit: int) -> list[dict[str, str]]:
    symbol = market_symbol(stock["code"])
    rows: list[dict[str, str]] = []
    fetched_at = now_text()
    for post in data.get("posts", [])[:limit]:
        title = html.unescape(str(post.get("title", "")))
        text = html.unescape(str(post.get("text", "")))
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 8 and not title:
            continue
        target = str(post.get("target", ""))
        if target.startswith("http"):
            detail_url = target
        elif target:
            detail_url = "https://xueqiu.com" + target
        else:
            detail_url = ""
        repost = str(post.get("retweet_count", "") or "0")
        comment = str(post.get("reply_count", "") or "0")
        like = str(post.get("like_count", "") or "0")
        heat_score = to_int(repost) * 3 + to_int(comment) * 2 + to_int(like)
        rows.append(
            {
                "fetched_at": fetched_at,
                "rank_speed": stock.get("rank_speed", ""),
                "code": stock.get("code", ""),
                "name": stock.get("name", ""),
                "symbol": symbol,
                "hot_rank": str(len(rows) + 1),
                "time_hint": api_time_hint(post.get("created_at")),
                "user": str(post.get("author", "") or ""),
                "title": title[:400],
                "text": text,
                "detail_url": detail_url,
                "repost_count": repost,
                "comment_count": comment,
                "like_count": like,
                "heat_score": str(heat_score),
                "source_status": "ok",
                "snapshot_path": str(snapshot_path),
            }
        )
    rows.sort(key=lambda row: to_int(row.get("heat_score", "")), reverse=True)
    for idx, row in enumerate(rows, start=1):
        row["hot_rank"] = str(idx)
    return rows


def normalize_posts(stock: dict[str, str], dom: dict[str, Any], snapshot_path: Path, limit: int) -> list[dict[str, str]]:
    symbol = market_symbol(stock["code"])
    rows: list[dict[str, str]] = []
    fetched_at = now_text()
    for post in dom.get("posts", [])[:limit]:
        raw_text = str(post.get("text", ""))
        links = post.get("links", []) or []
        detail_url = first_post_url(links)
        if stock["name"] not in raw_text and stock["code"] not in raw_text and symbol not in raw_text:
            continue
        title, text = clean_post_text(raw_text, stock)
        if len(text) < 8 and not title:
            continue
        repost, comment, like = parse_metrics(raw_text)
        heat_score = to_int(repost) * 3 + to_int(comment) * 2 + to_int(like)
        rows.append(
            {
                "fetched_at": fetched_at,
                "rank_speed": stock.get("rank_speed", ""),
                "code": stock.get("code", ""),
                "name": stock.get("name", ""),
                "symbol": symbol,
                "hot_rank": str(len(rows) + 1),
                "time_hint": first_time(links),
                "user": first_user(links),
                "title": title,
                "text": text[:1200],
                "detail_url": detail_url,
                "repost_count": repost,
                "comment_count": comment,
                "like_count": like,
                "heat_score": str(heat_score),
                "source_status": "ok",
                "snapshot_path": str(snapshot_path),
            }
        )
    rows.sort(key=lambda row: to_int(row.get("heat_score", "")), reverse=True)
    for idx, row in enumerate(rows, start=1):
        row["hot_rank"] = str(idx)
    return rows


def summarize(stock: dict[str, str], hot_posts: list[dict[str, str]], snapshot_path: Path, status: str) -> dict[str, str]:
    texts = [row.get("title", "") + " " + row.get("text", "") for row in hot_posts]
    counter: Counter[str] = Counter()
    for text in texts:
        for keyword in THEME_KEYWORDS:
            if keyword.lower() in text.lower():
                counter[keyword] += 1
    hot_terms = "、".join(term for term, _ in counter.most_common(8))
    samples = " || ".join((row.get("title") or row.get("text", ""))[:140] for row in hot_posts[:3])
    if hot_posts:
        explanation = f"热帖集中在：{hot_terms}" if hot_terms else "热帖有讨论，但主题词不够集中"
        evidence_value = "热帖是社区筛选后的市场解释线索，适合发现为什么涨、争议点和叙事主线"
        evidence_gap = "仍需公告、新闻、板块联动、盘口持续性做硬证据验证"
    else:
        explanation = "未抽到有效热帖"
        evidence_value = "暂无社区热帖证据"
        evidence_gap = status
    return {
        "fetched_at": now_text(),
        "rank_speed": stock.get("rank_speed", ""),
        "code": stock.get("code", ""),
        "name": stock.get("name", ""),
        "symbol": market_symbol(stock.get("code", "")),
        "comment_count": str(min(len(hot_posts), 6)),
        "hot_post_count": str(len(hot_posts)),
        "hot_terms": hot_terms,
        "community_explanation": explanation,
        "evidence_value": evidence_value,
        "evidence_gap": evidence_gap,
        "sample_comments": "",
        "sample_hot_posts": samples,
        "source_status": status,
        "snapshot_path": str(snapshot_path),
    }


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description="Collect Xueqiu hot posts through a logged-in Chrome API bridge.")
    add_mysql_args(parser)
    parser.add_argument("--mysql-window-id", default="", help="Read candidates from MySQL window instead of --top10-csv.")
    parser.add_argument("--top10-csv", type=Path, default=root / "data" / "stock" / "tdx_mover_speed_top10_latest.csv")
    parser.add_argument("--hot-posts-csv", type=Path, default=root / "data" / "stock" / "xueqiu_focus_hot_posts_latest.csv")
    parser.add_argument("--evidence-csv", type=Path, default=root / "data" / "stock" / "xueqiu_focus_evidence_latest.csv")
    parser.add_argument("--detail-cache", type=Path, default=root / "data" / "stock" / "cache" / "xueqiu_post_detail_cache.json")
    parser.add_argument("--no-detail-cache", action="store_true")
    parser.add_argument("--run-dir", type=Path, default=root / "runs" / "xueqiu_fast_bridge")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--hot-posts-per-stock", type=int, default=8)
    parser.add_argument("--api-sort", choices=["reply", "time"], default="reply")
    parser.add_argument("--skip-full-detail", action="store_true")
    parser.add_argument("--manual-verify-wait", type=int, default=120)
    parser.add_argument("--verify-retries", type=int, default=1)
    parser.add_argument("--pause", type=float, default=1.5)
    parser.add_argument("--timeout", type=int, default=80)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.run_dir.mkdir(parents=True, exist_ok=True)
    if args.mysql_enabled:
        if not args.mysql_window_id:
            print("mysql_window_id_missing")
            return 1
        stocks = window_evidence_candidate_rows(mysql_config_from_args(args), args.mysql_window_id)[: args.limit]
    else:
        stocks = read_top(args.top10_csv, args.limit)
    detail_cache_enabled = not args.no_detail_cache
    detail_cache: dict[str, Any] = read_detail_cache(args.detail_cache) if detail_cache_enabled else {}
    all_hot_posts: list[dict[str, str]] = []
    evidence_rows: list[dict[str, str]] = []
    had_error = False

    for stock in stocks:
        symbol = market_symbol(stock["code"])
        snapshot_path = args.run_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{symbol}.fast_snapshot.txt"
        status = "ok"
        hot_posts: list[dict[str, str]] = []
        cache_hit_count = 0
        detail_fetch_count = 0
        try:
            try:
                open_symbol(symbol, args.run_dir, args.pause, args.timeout)
                api_data, snapshot = run_api_fetch(
                    symbol,
                    args.run_dir,
                    args.pause,
                    args.timeout,
                    args.hot_posts_per_stock,
                    args.api_sort,
                    False,
                    args.manual_verify_wait,
                    args.verify_retries,
                )
                snapshot_path.write_text(snapshot, encoding="utf-8")
                if str(api_data.get("about", "")) not in {"", symbol}:
                    status = "api_symbol_mismatch"
                else:
                    if not args.skip_full_detail:
                        missing_ids, cache_hit_count = missing_detail_ids(api_data, detail_cache, args.hot_posts_per_stock)
                        detail_fetch_count = len(missing_ids)
                        if missing_ids:
                            detail_data, detail_snapshot = run_api_detail_fetch(
                                symbol,
                                args.run_dir,
                                args.pause,
                                args.timeout,
                                missing_ids,
                                args.manual_verify_wait,
                                args.verify_retries,
                            )
                            if detail_snapshot:
                                snapshot_path.write_text(snapshot + "\n\nXQ_DETAIL_SNAPSHOT\n" + detail_snapshot, encoding="utf-8")
                            changed = update_detail_cache(detail_cache, symbol, detail_data.get("posts", []))
                            if detail_cache_enabled and changed:
                                write_detail_cache(args.detail_cache, detail_cache)
                    elif detail_cache_enabled:
                        _, cache_hit_count = missing_detail_ids(api_data, detail_cache, args.hot_posts_per_stock)
                    if detail_cache:
                        api_data = merge_api_data_with_detail_cache(api_data, detail_cache)
                    hot_posts = normalize_api_posts(stock, api_data, snapshot_path, args.hot_posts_per_stock)
                    if not hot_posts:
                        status = "api_no_hot_posts"
                    elif not args.skip_full_detail:
                        status = f"ok;cache_hit={cache_hit_count};detail_fetch={detail_fetch_count}"
                    elif cache_hit_count:
                        status = f"ok;cache_hit={cache_hit_count};detail_fetch=skipped"
            except Exception as api_exc:
                if isinstance(api_exc, VerificationRequired):
                    status = f"need_manual_verify:{api_exc}"
                    had_error = True
                    hot_posts = []
                else:
                    open_symbol(symbol, args.run_dir, args.pause, args.timeout)
                    dom, snapshot = run_dom_extract(symbol, args.run_dir, args.pause, args.timeout, args.hot_posts_per_stock + 4)
                    snapshot_path.write_text(snapshot, encoding="utf-8")
                    if symbol not in str(dom.get("url", "")) and stock["name"] not in str(dom.get("title", "")):
                        status = f"api_failed_dom_navigation_mismatch:{type(api_exc).__name__}:{api_exc}"
                    else:
                        hot_posts = normalize_posts(stock, dom, snapshot_path, args.hot_posts_per_stock)
                        status = "dom_fallback_ok" if hot_posts else f"api_failed_dom_no_hot_posts:{type(api_exc).__name__}:{api_exc}"
        except Exception as exc:
            status = f"error:{type(exc).__name__}:{exc}"
            had_error = True

        all_hot_posts.extend(hot_posts)
        evidence_rows.append(summarize(stock, hot_posts, snapshot_path, status))
        print(f"{symbol} {stock.get('name', '')}: {status}, hot_posts={len(hot_posts)}")

    if had_error and not all_hot_posts:
        print("write_outputs=skipped_no_posts_preserve_previous")
        print(f"detail_cache={args.detail_cache if detail_cache_enabled else 'disabled'}")
        return 1

    write_csv(args.hot_posts_csv, all_hot_posts, HOT_POST_COLUMNS)
    write_csv(args.evidence_csv, evidence_rows, EVIDENCE_COLUMNS)
    print(f"hot_posts_csv={args.hot_posts_csv}")
    print(f"evidence_csv={args.evidence_csv}")
    return 1 if had_error and not all_hot_posts else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
深圳线下活动聚合 -> iCalendar 订阅源（零新账号）
- 数据源：RSSHub 公共实例（feeds.json 可配置）
- 解析：正则（豆瓣同城描述已半结构化，无需 LLM / API Key）
- 产出：events.json（去重状态）、public/events.ics（日历订阅）、public/index.html（看板）

用法：python shenzhen_events.py
"""
import json
import re
import os
import sys
import html
import hashlib
import datetime
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.abspath(__file__))
FEEDS_FILE = os.path.join(BASE, "feeds.json")
STATE_FILE = os.path.join(BASE, "events.json")
# 产物输出到项目根目录：便于 GitHub Pages（main 分支 /(root)）直接 serve，
# 也便于本地直接双击 index.html 预览。
ICS_FILE = os.path.join(BASE, "events.ics")
HTML_FILE = os.path.join(BASE, "index.html")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 深圳真实行政区 -> 计划单选值
PLAN_DISTRICTS = {
    "福田区": "福田", "南山区": "南山", "罗湖区": "罗湖",
    "宝安区": "宝安", "龙华区": "龙华", "龙岗区": "龙岗",
    "坪山区": "其他", "盐田区": "其他", "光明区": "其他", "大鹏新区": "其他",
}
# 豆瓣类型 -> 计划类型
TYPE_MAP = {
    "展览": "展览", "音乐": "演出", "戏剧": "演出", "电影": "演出", "话剧": "演出",
    "讲座": "讲座", "课程": "讲座", "沙龙": "讲座",
    "聚会": "其他", "运动": "其他", "旅行": "其他", "公益": "其他",
    "市集": "市集", "亲子": "亲子",
}
PLAN_TYPES = ["展览", "演出", "讲座", "市集", "亲子", "其他"]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def parse_douban_event(item):
    title = (item.findtext("title") or "").strip()
    link = (item.findtext("link") or "").strip()
    desc = item.findtext("description") or ""
    text = re.sub(r"<[^>]+>", " ", desc)
    text = re.sub(r"\s+", " ", text).strip()
    m = re.search(
        r"(\d{4}-\d{2}-\d{2})(?:\s*(?:~|至|—|-)\s*(\d{4}-\d{2}-\d{2}))?"
        r"\s*/\s*(.*?)\s*/\s*(.*?)\s*/\s*(.*)$",
        text,
    )
    if not m:
        return None
    date_start = m.group(1)
    date_end = m.group(2)
    place = (m.group(3) or "").strip()
    dtype = (m.group(4) or "").strip()
    price = (m.group(5) or "").strip()

    district_raw = "其他"
    location = place
    sp = place.split(" ", 1)
    if sp[0].endswith("区"):
        district_raw = sp[0]
        location = sp[1].strip() if len(sp) > 1 else ""

    district = PLAN_DISTRICTS.get(district_raw, "其他")
    category = TYPE_MAP.get(dtype, "其他")
    if category not in PLAN_TYPES:
        category = "其他"
    if any(k in title for k in ("亲子", "儿童", "少儿")):
        category = "亲子"

    if "免费" in price:
        is_free = "免费"
    elif re.search(r"\d", price):
        is_free = "收费"
    else:
        is_free = "未知"

    return {
        "title": title,
        "date_start": date_start,
        "date_end": date_end,
        "district_raw": district_raw,
        "district": district,
        "location": location,
        "category": category,
        "price": price,
        "is_free": is_free,
        "link": link,
    }


PARSERS = {"douban_event": parse_douban_event}


def process_feed(feed):
    raw = fetch(feed["url"])
    root = ET.fromstring(raw)
    out = []
    for item in root.iter("item"):
        parser = PARSERS.get(feed.get("parser", "douban_event"), parse_douban_event)
        ev = parser(item)
        if ev:
            ev["source"] = feed["name"]
            ev["id"] = hashlib.md5(
                (ev["title"] + ev["date_start"] + ev["link"]).encode("utf-8")
            ).hexdigest()[:16]
            out.append(ev)
    return out


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"events": []}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def ics_date(d):
    return d.replace("-", "")


def build_ics(events):
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    L = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//ShenzhenEvents//ZH//", "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH", "X-WR-CALNAME:深圳线下活动",
        "X-WR-TIMEZONE:Asia/Shanghai",
    ]
    for ev in events:
        ds = ics_date(ev["date_start"])
        if ev.get("date_end"):
            de = datetime.date.fromisoformat(ev["date_end"]) + datetime.timedelta(days=1)
            de_str = de.strftime("%Y%m%d")
        else:
            de_str = ""
        summary = ev["title"].replace(",", "，").replace(";", "；").replace("\n", "")
        location = (ev["district_raw"] + " " + ev["location"]).strip() \
            .replace(",", "，").replace(";", "；")
        desc = "\\n".join([
            "类型: " + ev["category"],
            "价格: " + (ev["price"] or "未知"),
            "区域: " + ev["district"],
            "来源: " + ev["source"],
            "链接: " + ev["link"],
        ])
        L.append("BEGIN:VEVENT")
        L.append("UID:" + ev["id"] + "@shenzhen-events")
        L.append("DTSTAMP:" + now)
        L.append("DTSTART;VALUE=DATE:" + ds)
        if de_str:
            L.append("DTEND;VALUE=DATE:" + de_str)
        L.append("SUMMARY:" + summary)
        L.append("LOCATION:" + location)
        L.append("DESCRIPTION:" + desc)
        L.append("CATEGORIES:" + ev["category"])
        L.append("URL:" + ev["link"])
        L.append("END:VEVENT")
    L.append("END:VCALENDAR")
    return "\r\n".join(L)


def build_html(events):
    today = datetime.date.today().isoformat()
    cards = []
    for ev in events:
        free_badge = {"免费": "free", "收费": "paid", "未知": "unk"}.get(ev["is_free"], "unk")
        cards.append(f"""
        <div class="card" data-district="{ev['district']}" data-category="{ev['category']}">
          <div class="date">{ev['date_start']}{(' ~ '+ev['date_end']) if ev.get('date_end') else ''}</div>
          <div class="title"><a href="{html.escape(ev['link'])}" target="_blank" rel="noopener">{html.escape(ev['title'])}</a></div>
          <div class="meta">
            <span class="tag cat">{html.escape(ev['category'])}</span>
            <span class="tag dist">{html.escape(ev['district'])}</span>
            <span class="tag {free_badge}">{html.escape(ev['is_free'])}</span>
            <span class="price">{html.escape(ev['price'] or '未知')}</span>
          </div>
          <div class="loc">{html.escape(ev['district_raw'] + ' ' + ev['location'])}</div>
        </div>""")
    body = "\n".join(cards) if cards else '<p class="empty">暂无未来活动</p>'
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>深圳线下活动看板</title>
<style>
  :root {{ --bg:#f5f7fa; --card:#fff; --ink:#1f2329; --muted:#6b7280; --line:#e5e7eb; --blue:#2563eb; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; background:var(--bg); color:var(--ink); }}
  header {{ position:sticky; top:0; background:#fff; border-bottom:1px solid var(--line); padding:14px 18px; display:flex; flex-wrap:wrap; gap:10px; align-items:center; }}
  h1 {{ font-size:18px; margin:0; }}
  .count {{ color:var(--muted); font-size:13px; }}
  .sub {{ background:var(--blue); color:#fff; border:none; padding:8px 14px; border-radius:8px; text-decoration:none; font-size:13px; }}
  .filters {{ display:flex; flex-wrap:wrap; gap:6px; padding:12px 18px; }}
  .chip {{ border:1px solid var(--line); background:#fff; border-radius:999px; padding:4px 12px; font-size:12px; cursor:pointer; color:var(--muted); }}
  .chip.on {{ background:var(--blue); color:#fff; border-color:var(--blue); }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:12px; padding:0 18px 32px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; }}
  .date {{ font-size:12px; color:var(--blue); font-weight:600; }}
  .title {{ font-size:15px; font-weight:600; margin:6px 0; line-height:1.4; }}
  .title a {{ color:var(--ink); text-decoration:none; }}
  .meta {{ display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin-bottom:8px; }}
  .tag {{ font-size:11px; padding:2px 8px; border-radius:6px; background:#eef2ff; color:#3730a3; }}
  .tag.dist {{ background:#ecfdf5; color:#047857; }}
  .tag.free {{ background:#ecfdf5; color:#047857; }}
  .tag.paid {{ background:#fef2f2; color:#b91c1c; }}
  .tag.unk {{ background:#f3f4f6; color:#6b7280; }}
  .price {{ font-size:12px; color:var(--muted); }}
  .loc {{ font-size:12px; color:var(--muted); }}
  .empty {{ color:var(--muted); padding:40px; text-align:center; }}
</style></head>
<body>
<header>
  <h1>深圳线下活动看板</h1>
  <span class="count">共 {len(events)} 场 · 更新于 {today}</span>
  <a class="sub" href="events.ics">订阅日历 (webcal)</a>
</header>
<div class="filters" id="filters"></div>
<div class="grid" id="grid">{body}</div>
<script>
  const cats=["展览","演出","讲座","市集","亲子","其他"];
  const dists=["福田","南山","罗湖","宝安","龙华","龙岗","其他"];
  const f=document.getElementById('filters');
  let active={{cat:'',dist:''}};
  function chip(label,kind,val){{
    const b=document.createElement('button');
    b.className='chip'+(active[kind]===val?' on':'');
    b.textContent=label;
    b.onclick=()=>{{ active[kind]=active[kind]===val?'':val; render(); }};
    return b;
  }}
  function render(){{
    f.innerHTML='';
    f.appendChild(chip('全部类型','cat',''));
    cats.forEach(c=>f.appendChild(chip(c,'cat',c)));
    f.appendChild(chip('全部区域','dist',''));
    dists.forEach(d=>f.appendChild(chip(d,'dist',d)));
    document.querySelectorAll('.card').forEach(c=>{{
      const ok=(!active.cat||c.dataset.category===active.cat)&&(!active.dist||c.dataset.district===active.dist);
      c.style.display=ok?'':'none';
    }});
    document.querySelectorAll('.chip').forEach((b,i)=>{{
      const label=b.textContent;
      const kind= i< (1+cats.length) ? 'cat':'dist';
      const val= i===0?'': (i<=cats.length? cats[i-1] : dists[i-(2+cats.length)]);
      b.className='chip'+((kind==='cat'&&active.cat===val)||(kind==='dist'&&active.dist===val)?' on':'');
    }});
  }}
  render();
</script>
</body></html>"""


def main():
    with open(FEEDS_FILE, encoding="utf-8") as f:
        feeds = json.load(f)
    state = load_state()
    existing = {e["id"]: e for e in state.get("events", [])}
    today = datetime.date.today()
    added = []
    for feed in feeds:
        try:
            evs = process_feed(feed)
        except Exception as e:
            print("feed failed:", feed["name"], "->", e, file=sys.stderr)
            continue
        for ev in evs:
            try:
                d = datetime.date.fromisoformat(ev["date_start"])
            except Exception:
                d = today
            if d < today:
                continue
            if ev["id"] in existing:
                continue
            existing[ev["id"]] = ev
            added.append(ev)
    merged = list(existing.values())
    merged.sort(key=lambda e: e["date_start"])
    state["events"] = merged
    save_state(state)
    os.makedirs(BASE, exist_ok=True)
    with open(ICS_FILE, "w", encoding="utf-8", newline="") as f:
        f.write(build_ics(merged))
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(build_html(merged))
    print(f"OK 总事件={len(merged)} 本次新增={len(added)} -> {ICS_FILE}")


if __name__ == "__main__":
    main()

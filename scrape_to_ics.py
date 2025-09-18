import os, re, hashlib, datetime as dt
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup

# ===== 需要的基本配置（已为你填好，后面可再调）=====
COURSE_URL = "https://wumingshi.xiaosaas.com/org/teacher.html?xiaov=175717602431&flagl=a"
NEED_LOGIN = True
COOKIE_STRING = os.getenv("COOKIES", "")  # 我们稍后把 Cookie 填到 GitHub Secrets 里

# 先按常见表格结构猜的选择器，跑通后可再微调
ROW_SELECTOR = ".ant-table-tbody > tr"
COLS = {
    "date": "td:nth-child(1)",
    "start": "td:nth-child(2)",
    "end": "td:nth-child(3)",
    "title": "td:nth-child(4)",
    "location": "td:nth-child(5)",
    "desc": "td:nth-child(6)",
}

LOCAL_TZ = "Asia/Shanghai"            # 时区（苹果端会自动换算显示）
CAL_NAME = "Company Courses"          # 订阅日历显示的名称
ICS_FILENAME = "schedule.ics"         # 输出文件名（Pages 会发布它）
# =====================================================

def norm_date(s: str) -> dt.date:
    s = s.strip()
    for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%Y.%m.%d", "%d-%m-%Y", "%m-%d-%Y"]:
        try:
            return dt.datetime.strptime(s, fmt).date()
        except:
            pass
    s2 = s.replace("年", "-").replace("月", "-").replace("日", "")
    s2 = re.sub(r"[./]", "-", s2)
    parts = [p for p in s2.split("-") if p.strip()]
    today = dt.date.today()
    if len(parts) == 2:
        m, d = map(int, parts)
        return dt.date(today.year, m, d)
    raise ValueError(f"无法识别日期: {s}")

def norm_time(s: str) -> dt.time:
    s = s.strip().lower().replace("：", ":")
    s = s.replace("am", " am").replace("pm", " pm")
    for fmt in ["%H:%M", "%I:%M %p", "%H.%M", "%I %p", "%H"]:
        try:
            return dt.datetime.strptime(s, fmt).time()
        except:
            pass
    m = re.match(r"^(\d{1,2})$", s)
    if m:
        return dt.time(int(m.group(1)), 0)
    raise ValueError(f"无法识别时间: {s}")

def text_of(el): return (el.get_text(" ", strip=True) if el else "").strip()
def css_select(el, selector): 
    res = el.select_one(selector)
    return text_of(res)

def uid_for(*parts):
    raw = "||".join([p or "" for p in parts])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()+"@xiaosaas-course"

def build_ics(events):
    tz = ZoneInfo(LOCAL_TZ)
    now_utc = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    esc = lambda s: s.replace("\\","\\\\").replace(",","\\,").replace(";","\\;").replace("\n","\\n")
    lines = [
        "BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//Course Sync//Auto//EN",
        f"X-WR-CALNAME:{CAL_NAME}","CALSCALE:GREGORIAN","METHOD:PUBLISH",
    ]
    for ev in events:
        start = ev["start"].astimezone(tz); end = ev["end"].astimezone(tz)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{ev['uid']}",
            f"DTSTAMP:{now_utc}",
            f"DTSTART;TZID={LOCAL_TZ}:{start.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID={LOCAL_TZ}:{end.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{esc(ev['title'])}",
            f"LOCATION:{esc(ev['location'])}",
            f"DESCRIPTION:{esc(ev['desc'])}",
            # 如需提醒可加 VALARM：
            # "BEGIN:VALARM","TRIGGER:-PT10M","ACTION:DISPLAY","DESCRIPTION:Reminder","END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"

def scrape():
    headers = {"User-Agent": "Mozilla/5.0"}
    if NEED_LOGIN and COOKIE_STRING:
        headers["Cookie"] = COOKIE_STRING
    r = requests.get(COURSE_URL, headers=headers, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    tz = ZoneInfo(LOCAL_TZ)
    events = []
    for row in soup.select(ROW_SELECTOR):
        try:
            date_s  = css_select(row, COLS["date"])
            start_s = css_select(row, COLS["start"])
            end_s   = css_select(row, COLS["end"])
            title   = css_select(row, COLS["title"]) or "Course"
            location= css_select(row, COLS["location"])
            desc    = css_select(row, COLS["desc"])

            d  = norm_date(date_s)
            t1 = norm_time(start_s)
            t2 = norm_time(end_s)

            start_dt = dt.datetime.combine(d, t1, tzinfo=tz)
            end_dt   = dt.datetime.combine(d, t2, tzinfo=tz)
            if end_dt <= start_dt:
                end_dt = start_dt + dt.timedelta(minutes=90)

            uid = uid_for(str(d), start_s, end_s, title, location)
            events.append({
                "uid": uid, "start": start_dt, "end": end_dt,
                "title": title, "location": location or "", "desc": desc or "",
            })
        except Exception as e:
            print("Row parse error:", e)
            continue
    return events

if __name__ == "__main__":
    events = scrape()
    events.sort(key=lambda x: x["start"])
    ics = build_ics(events)
    with open(ICS_FILENAME, "w", encoding="utf-8") as f:
        f.write(ics)
    print(f"Generated {ICS_FILENAME} with {len(events)} events.")

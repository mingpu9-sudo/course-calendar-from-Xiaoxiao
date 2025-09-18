import os, re, hashlib, datetime as dt
from zoneinfo import ZoneInfo
import requests

# ========= 必改：把这条替换成你在 Network 里复制到的“timetable?ym=...”完整URL =========
API_URL_SAMPLE = "https://xapi.xiaosaas.com/rest/opp/fteacher/timetable?ym=2025-09&seeme=&tok=e1d8d4f601cedca7d8b7812059499494&lang=cn"  # ← 粘贴你的URL
# ==================================================================================

NEED_LOGIN = True
COOKIE_STRING = os.getenv("COOKIES", "")  # 你已经在 Secrets 里加好了

# 日历显示设置
LOCAL_TZ = "Asia/Shanghai"
CAL_NAME = "WMS Courses"
ICS_FILENAME = "schedule.ics"

# —— 工具函数 ——
def month_str(d: dt.date) -> str:
    return d.strftime("%Y-%m")  # "2025-09"

def make_urls():
    """
    这个函数会自动把 API_URL_SAMPLE 里 ym=YYYY-MM 替换为
    当月、下月、上月 三个月，尽可能多抓一点（防止跨月）。
    如果你的URL里没有 ym 参数，就只用原始URL。
    """
    m = re.search(r"(ym=)\d{4}-\d{2}", API_URL_SAMPLE)
    if not m:
        return [API_URL_SAMPLE]
    today = dt.date.today()
    first = today.replace(day=1)
    # 上月、本月、下月
    months = [first - dt.timedelta(days=1), first, (first + dt.timedelta(days=32))]
    months = [d.replace(day=1) for d in months]
    urls = []
    for d in months:
        ym = month_str(d)
        urls.append(re.sub(r"(ym=)\d{4}-\d{2}", r"\1"+ym, API_URL_SAMPLE))
    return urls

def parse_ms_or_str(date_str: str, time_str: str, tz: dt.tzinfo) -> dt.datetime:
    # 输入类似 "2025-09-01" + "07:00" → 返回带时区的 datetime
    dt_obj = dt.datetime.strptime(date_str.strip()+" "+time_str.strip(), "%Y-%m-%d %H:%M")
    return dt_obj.replace(tzinfo=tz)

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
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"

def fetch_json(url):
    headers = {"User-Agent":"Mozilla/5.0"}
    if COOKIE_STRING:
        headers["Cookie"] = COOKIE_STRING
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()

def scrape_via_api():
    tz = ZoneInfo(LOCAL_TZ)
    events = []
    for url in make_urls():
        j = fetch_json(url)

        # 结构：{ "data": [ { "date": "2025-09-01", "schedules": [ {...} ] }, ... ] }
        days = j.get("data", [])
        for day in days:
            date_s = str(day.get("date", "")).strip()
            for item in day.get("schedules", []):

                # 你提供的JSON里有：starttimeStr / endtimeStr（最可靠）
                start_str = (item.get("starttimeStr") or "").strip()   # "2025-09-01 07:00"
                end_str   = (item.get("endtimeStr") or "").strip()     # "2025-09-01 23:55"

                # 如果没有 *_Str，就用毫秒时间戳（以防万一）
                if not start_str and item.get("starttime"):
                    start_ms = int(item["starttime"]) // 1000
                    start_dt = dt.datetime.fromtimestamp(start_ms, tz=tz)
                else:
                    start_dt = dt.datetime.strptime(start_str, "%Y-%m-%d %H:%M").replace(tzinfo=tz)

                if not end_str and item.get("endtime"):
                    end_ms = int(item["endtime"]) // 1000
                    end_dt = dt.datetime.fromtimestamp(end_ms, tz=tz)
                else:
                    end_dt = dt.datetime.strptime(end_str, "%Y-%m-%d %H:%M").replace(tzinfo=tz)

                # 标题优先级：courseName 其一；如果没有，用 reason；再不行，用“课程”
                course_name = (item.get("courseName") or "").strip() if item.get("courseName") else ""
                reason = (item.get("reason") or "").strip() if item.get("reason") else ""
                title = course_name or (("【" + reason + "】") if reason else "课程")

                # 地点/备注
                location = (item.get("place") or item.get("campusname") or "") or ""
                teacher_name = ""
                if isinstance(item.get("teacher"), dict):
                    teacher_name = item["teacher"].get("name") or ""
                desc = f"教师: {teacher_name}".strip()

                # 兜底：如果结束早于开始，补上80分钟（你接口里 duration=80）
                if end_dt <= start_dt:
                    end_dt = start_dt + dt.timedelta(minutes=80)

                uid = uid_for(date_s, start_str or str(item.get("starttime")), title, location)
                events.append({
                    "uid": uid, "start": start_dt, "end": end_dt,
                    "title": title, "location": location, "desc": desc
                })
    return events

if __name__ == "__main__":
    events = scrape_via_api()
    events.sort(key=lambda x: x["start"])
    ics = build_ics(events)
    with open(ICS_FILENAME, "w", encoding="utf-8") as f:
        f.write(ics)
    print(f"Generated {ICS_FILENAME} with {len(events)} events.")

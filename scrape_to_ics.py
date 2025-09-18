import os, re, hashlib, datetime as dt
from zoneinfo import ZoneInfo
import requests

# ========= 必改：把这条替换成你在 Network 里复制到的“timetable?ym=...”完整URL =========
API_URL_SAMPLE = "https://xapi.xiaosaas.com/rest/opp/fteacher/timetable?ym=2025-09&seeme=&tok=e1d8d4f601cedca7d8b7812059499494&lang=cn"
# ==================================================================================

NEED_LOGIN = True
COOKIE_STRING = os.getenv("COOKIES", "")  # 已在仓库 Secrets 里配置

# 日历显示设置
LOCAL_TZ = "Asia/Shanghai"
CAL_NAME = "Company Courses"
ICS_FILENAME = "schedule.ics"

# —— 工具函数 ——
def month_str(d: dt.date) -> str:
    return d.strftime("%Y-%m")  # "2025-09"

def make_urls():
    """
    把 API_URL_SAMPLE 里 ym=YYYY-MM 替换为 上月/本月/下月，跨月也能抓到。
    如果URL里没有 ym 参数，就只返回原始URL。
    """
    m = re.search(r"(ym=)\d{4}-\d{2}", API_URL_SAMPLE)
    if not m:
        return [API_URL_SAMPLE]
    today = dt.date.today()
    first = today.replace(day=1)
    months = [ (first - dt.timedelta(days=1)).replace(day=1),
               first,
               (first + dt.timedelta(days=32)).replace(day=1) ]
    urls = []
    for d in months:
        ym = month_str(d)
        urls.append(re.sub(r"(ym=)\d{4}-\d{2}", r"\1"+ym, API_URL_SAMPLE))
    return urls

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

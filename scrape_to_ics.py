import os, re, hashlib, datetime as dt
from zoneinfo import ZoneInfo
import requests

# ←←← 把这条替换成你在 Network 里复制到的 timetable 完整 URL（包含 ym=YYYY-MM）
API_URL_SAMPLE = "https://xapi.xiaosaas.com/rest/opp/fteacher/timetable?ym=2025-09&seeme=&tok=e1d8d4f601cedca7d8b7812059499494&lang=cn"

NEED_LOGIN = True
COOKIE_STRING = os.getenv("COOKIES", "")

LOCAL_TZ = "Asia/Shanghai"
CAL_NAME = "Company Courses"
ICS_FILENAME = "schedule.ics"

# ---------- 月份范围：前2 ~ 后5，共8个月 ----------
def shift_month(d: dt.date, delta: int) -> dt.date:
    y = d.year + (d.month - 1 + delta) // 12
    m = (d.month - 1 + delta) % 12 + 1
    return dt.date(y, m, 1)

def make_urls():
    if not re.search(r"(ym=)\d{4}-\d{2}", API_URL_SAMPLE):
        return [API_URL_SAMPLE]
    today = dt.date.today()
    base = dt.date(today.year, today.month, 1)
    urls = []
    for k in range(-2, 6):  # 前2 ~ 后5
        ym = shift_month(base, k).strftime("%Y-%m")
        u = re.sub(r"(ym=)\d{4}-\d{2}", r"\1"+ym, API_URL_SAMPLE)
        urls.append(u)
    # 去重
    seen, dedup = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u); dedup.append(u)
    return dedup

# ---------- 生成 ICS ----------
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

# ---------- 网络与解析 ----------
def fetch_json(url):
    headers = {"User-Agent":"Mozilla/5.0"}
    if COOKIE_STRING:
        headers["Cookie"] = COOKIE_STRING
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()

def safe_parse_dt(s: str, tz: dt.tzinfo):
    if not s:
        return None
    return dt.datetime.strptime(s.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=tz)

def scrape_via_api():
    tz = ZoneInfo(LOCAL_TZ)
    events = []
    for url in make_urls():
        j = fetch_json(url)
        days = j.get("data") or []
        for day in days:
            date_s = str(day.get("date", "")).strip()
            for item in (day.get("schedules") or []):
                # 时间：优先 *_Str；否则毫秒时间戳兜底
                start_str = (item.get("starttimeStr") or "").strip()
                end_str   = (item.get("endtimeStr") or "").strip()
                start_dt = safe_parse_dt(start_str, tz)
                end_dt   = safe_parse_dt(end_str, tz)
                if start_dt is None and item.get("starttime"):
                    start_dt = dt.datetime.fromtimestamp(int(item["starttime"])//1000, tz=tz)
                if end_dt is None and item.get("endtime"):
                    end_dt   = dt.datetime.fromtimestamp(int(item["endtime"])//1000, tz=tz)
                if start_dt is None or end_dt is None:
                    continue
                if end_dt <= start_dt:
                    end_dt = start_dt + dt.timedelta(minutes=80)  # 接口里 duration=80

                # 取班级 + 课程名（尽量全）
                clz = (item.get("clzName") or "").strip()
                course = (item.get("courseName") or "").strip()
                reason = (item.get("reason") or "").strip()   # 有时会是“固休”等
                # 标题规则：班级｜课程名；都没有时用【reason】；再不行“课程”
                title_parts = [p for p in [clz, course] if p]
                if title_parts:
                    title = "｜".join(title_parts)
                elif reason:
                    title = f"【{reason}】"
                else:
                    title = "课程"

                # 地点 & 描述（把完整标题也放进描述，防止月视图折行看不全）
                location = (item.get("place") or item.get("campusname") or "") or ""
                teacher_name = ""
                t = item.get("teacher")
                if isinstance(t, dict):
                    teacher_name = (t.get("name") or "").strip()

                desc_lines = []
                if title_parts:
                    desc_lines.append(f"标题: { '｜'.join(title_parts) }")
                if teacher_name:
                    desc_lines.append(f"教师: {teacher_name}")
                if location:
                    desc_lines.append(f"地点: {location}")
                desc = "\n".join(desc_lines) if desc_lines else ""

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

"""账单日历日期与旧时间字段之间的兼容转换。"""
import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def date_only(value):
    if value is None:
        return value
    if isinstance(value, datetime):
        raise ValueError("日期必须使用 YYYY-MM-DD，不包含时间")
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("日期必须使用 YYYY-MM-DD")
    return date.fromisoformat(value)


def local_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)


def midnight(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=SHANGHAI)


def resolve_range(start_at=None, end_at=None, start_date=None, end_date=None, *, required=False):
    start = midnight(start_date) if start_date is not None else local_datetime(start_at) if start_at else None
    end = midnight(end_date) if end_date is not None else local_datetime(end_at) if end_at else None
    if start_date is not None and start_at is not None and start != local_datetime(start_at):
        raise ValueError("开始日期与开始时间不一致")
    if end_date is not None and end_at is not None and end != local_datetime(end_at):
        raise ValueError("结束日期与结束时间不一致")
    if required and (start is None or end is None):
        raise ValueError("请提供开始日期和结束日期")
    if start is not None and end is not None and end <= start:
        raise ValueError("结束日期必须晚于开始日期，结束日期不包含在统计范围内")
    return start, end

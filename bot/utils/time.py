
import datetime


def now_date_str():
    return datetime.datetime.now().strftime("%Y-%m-%d")


def warn_date_str():
    utc = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    return utc.strftime("%Y-%m-%d")

import gzip
import hashlib
import io
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import httpx


async def match_keywords(match_list, lists) -> Tuple[Optional[str], List]:
    """字段列表匹配(仅匹配单个元素)

    Args:
        match_list (List): 待匹配列表
        lists (List): 匹配字符列表

    Returns:
        match_keywards (str/None):匹配到的字段
        match_list (List): 去除匹配字符后的列表
    """
    for each in lists:
        for kw in each.keywords:
            for match_kw in match_list:
                if match_kw == kw or match_kw.upper() == kw.upper() or match_kw.lower() == kw.lower():
                    match_list.remove(match_kw)
                    return each.match_keywords, match_list
    return None, match_list


def encode_gzip(bytes) -> str:
    """gzip压缩

    Args:
        bytes (bytes): 需要压缩的content

    Returns:
        str (str): 压缩后数据
    """
    buf = io.BytesIO(bytes)
    gf = gzip.GzipFile(fileobj=buf)
    return gf.read().decode('utf-8')


class FreqLimiter:
    def __init__(self, default_cd_seconds):
        self.next_time = defaultdict(float)
        self.default_cd = default_cd_seconds

    def check(self, key) -> bool:
        return bool(time.time() >= self.next_time[key])

    def start_cd(self, key, cd_time=0):
        self.next_time[key] = time.time() + (cd_time if cd_time > 0 else self.default_cd)

    def left_time(self, key) -> float:
        return self.next_time[key] - time.time()


class DailyNumberLimiter:
    tz = timezone(timedelta(hours=8))  # Asia/Shanghai 固定 UTC+8（中国无夏令时），原生替代 pytz

    def __init__(self, max_num):
        self.today = -1
        self.count = defaultdict(int)
        self.max = max_num

    def check(self, key) -> bool:
        now = datetime.now(self.tz)
        day = (now - timedelta(hours=5)).day
        if day != self.today:
            self.today = day
            self.count.clear()
        return bool(self.count[key] < self.max)

    def get_num(self, key):
        return self.count[key]

    def increase(self, key, num=1):
        self.count[key] += num

    def reset(self, key):
        self.count[key] = 0


async def download(url, path, proxy=None):
    async with httpx.AsyncClient(proxies=proxy) as client:
        resp = await client.get(url, timeout=20)
        content = resp.read()
        content = content.replace(b'\n', b'\r\n')
        with open(path, 'wb') as f:
            f.write(content)


async def byte2md5(bytes) -> str:
    """bytes转为md5

    Args:
        bytes (bytes): 原始数据

    Returns:
        str (str): md5
    """
    res = hashlib.md5(bytes).hexdigest()
    return res

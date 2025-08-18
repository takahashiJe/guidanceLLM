# backend/worker/app/services/information/web_crawler.py
from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_TENKI_CHOKAI_URL = "https://tenki.jp/mountain/famous/point-36/"  # 鳥海山（tenki.jp 構成変更に強い固定 URL にする）


# ------------------------------------------------------------
# text utility
# ------------------------------------------------------------

def _clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# ------------------------------------------------------------
# condition normalization（表記ゆれを正規化）
# ------------------------------------------------------------

_COND_MAP = {
    "晴れ": "晴れ",
    "はれ": "晴れ",
    "晴": "晴れ",
    "快晴": "晴れ",
    "薄曇り": "くもり",
    "曇り": "くもり",
    "くもり": "くもり",
    "曇": "くもり",
    "雨": "雨",
    "小雨": "雨",
    "にわか雨": "雨",
    "雷雨": "雨",
    "雪": "雪",
    "みぞれ": "雪",
    "暴風": "風",
    "強風": "風",
    "風": "風",
}

def _normalize_condition(s: str) -> str:
    t = _clean(s).lower()
    # 漢字/ひらがなの単純対応
    for k, v in _COND_MAP.items():
        if k in t or k in s:
            return v
    # 記述文の場合はキーワードで推定
    if re.search(r"晴|はれ|快晴", s):
        return "晴れ"
    if re.search(r"曇|くもり", s):
        return "くもり"
    if re.search(r"雨|雷", s):
        return "雨"
    if re.search(r"雪|みぞれ", s):
        return "雪"
    if re.search(r"風", s):
        return "風"
    return "不明"


# ------------------------------------------------------------
# date helpers
# ------------------------------------------------------------

def _guess_year_for_month_day(month: int, day: int, base: Optional[dt.date] = None) -> int:
    """
    "MM/DD" など年のない日付に対して、現在日に近い年を推定。
    - 直近過去の日付を優先（未来に飛びすぎない）
    """
    base = base or dt.date.today()
    y = base.year
    try_date = dt.date(y, month, day)
    if try_date > base:
        # 未来に行き過ぎる場合は前年に倒す（ニュース/概況は直近過去を参照する前提）
        return y - 1
    return y

def _parse_mmdd_to_iso(mmdd: str, base: Optional[dt.date] = None) -> str:
    """
    "MM/DD" -> "YYYY-MM-DD"（年は推定）
    """
    m = re.match(r"^\s*(\d{1,2})[/-](\d{1,2})\s*$", mmdd)
    if not m:
        raise ValueError(f"Invalid MM/DD: {mmdd}")
    month, day = int(m.group(1)), int(m.group(2))
    year = _guess_year_for_month_day(month, day, base=base)
    return dt.date(year, month, day).isoformat()


# ------------------------------------------------------------
# Crawler
# ------------------------------------------------------------

@dataclass
class TenkiCrawler:
    """
    tenki.jp の山ページから日次概況を抽出する責務のクラス。
    - fetch(): HTML を取得
    - parse_daily(): タイトル/本文を抽出してまとめテキストを返す
    """
    url: str = _TENKI_CHOKAI_URL
    timeout_sec: float = 10.0

    def fetch(self) -> Optional[str]:
        try:
            r = requests.get(self.url, timeout=self.timeout_sec)
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.warning("TenkiCrawler.fetch failed: %s", e)
            return None

    def _select_first_text(self, soup: BeautifulSoup, selectors: Iterable[str]) -> Optional[str]:
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                t = _clean(el.get_text())
                if t:
                    return t
        return None

    def _select_paragraphs(self, soup: BeautifulSoup, selectors: Iterable[str], min_len: int = 20) -> List[str]:
        for sel in selectors:
            items = []
            for p in soup.select(sel):
                t = _clean(p.get_text())
                if len(t) >= min_len and "tenki.jp" not in t:
                    items.append(t)
            if items:
                return items
        return []

    def parse_daily(self, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")

        # タイトル候補（見出し）
        title = self._select_first_text(soup, [
            "h1", "h2", "h3", "header h1", "article h1",
            ".weather-title", ".title", ".headline",
        ]) or "鳥海山の天気概況"

        # 本文候補（複数セレクタでフェイルセーフ）
        paragraphs = self._select_paragraphs(soup, [
            "div#weather-news p",
            "div.weather-news p",
            "article p",
            "section p",
            "div.summary p",
            ".weather-detail p",
            "p",
        ], min_len=20)

        body = " ".join(paragraphs[:4]) if paragraphs else "（本文の抽出に失敗しました）"
        return f"{title}: {body}"


# ------------------------------------------------------------
# Public function used by information_service
# ------------------------------------------------------------

def get_tenkijp_chokai_daily(date: dt.date) -> str:
    """
    tenki.jp の鳥海山ページから、当日（または直近）の概況文を返す。
    - 実際にはページ内に「MM/DD」等の明示がない場合もあるため、
      ページ上の最新テキストを取得する。
    - 取得できない場合はフォールバック文を返し、import/起動は壊さない。
    """
    crawler = TenkiCrawler()
    html = crawler.fetch()
    if not html:
        return f"（{date:%Y-%m-%d}の鳥海山情報を取得できませんでした）"
    try:
        # 必要に応じてページ内から「MM/DD」を拾って日付を正規化したい場合は、
        # 下記のような処理を足す（存在しない場合はスキップ）
        #   mmdd = _search_mmdd_in_html(html)  # 実装例: 正規表現で '3/18' などを拾う
        #   iso = _parse_mmdd_to_iso(mmdd, base=date)
        text = crawler.parse_daily(html)
        return text
    except Exception as e:
        logger.warning("get_tenkijp_chokai_daily parse failed: %s", e)
        return f"（{date:%Y-%m-%d}の鳥海山情報の解析に失敗しました）"

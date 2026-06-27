# -*- coding: utf-8 -*-
"""
news_analyzer.py — محلل الأخبار + تقويم الأحداث المجدولة
يجلب أخبار كل رمز، يحلل المشاعر، ويتوقع نتيجة الأخبار المجدولة قبل صدورها
"""
import threading, time, logging, re, json
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, List
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger("news_analyzer")

WATCHED_SYMBOLS = [
    'QQQ', 'SPY', 'NVDA', 'MSFT', 'META', 'AAPL', 'AMD',
    'AMZN', 'TSLA', 'NFLX', 'GOOGL', 'LLY',
]

POSITIVE_WORDS = [
    'beats', 'beat', 'surpasses', 'exceeds', 'record', 'growth', 'rally',
    'upgrade', 'buy', 'bullish', 'strong', 'gains', 'rises', 'jumps',
    'soars', 'profit', 'revenue', 'outperform', 'positive', 'higher',
    'surge', 'boost', 'impressive', 'better', 'optimistic', 'win',
    'partnership', 'deal', 'contract', 'innovation', 'breakthrough',
]

NEGATIVE_WORDS = [
    'misses', 'miss', 'falls', 'drops', 'decline', 'loss', 'weak',
    'downgrade', 'sell', 'bearish', 'concern', 'warning', 'risk',
    'cut', 'below', 'disappoints', 'disappointing', 'lower', 'crash',
    'recession', 'inflation', 'layoffs', 'lawsuit', 'investigation',
    'recall', 'hack', 'breach', 'fine', 'penalty', 'worse', 'fear',
]

HIGH_IMPACT_WORDS = [
    'earnings', 'results', 'revenue', 'profit', 'guidance', 'forecast',
    'fed', 'rate', 'inflation', 'gdp', 'jobs', 'cpi', 'merger', 'acquisition',
    'sec', 'lawsuit', 'bankruptcy', 'ceo', 'layoffs', 'recall',
]

SYMBOL_KEYWORDS = {
    'AAPL':  ['apple', 'iphone', 'mac', 'ios', 'tim cook'],
    'MSFT':  ['microsoft', 'azure', 'windows', 'copilot', 'satya'],
    'NVDA':  ['nvidia', 'gpu', 'cuda', 'jensen huang', 'h100', 'blackwell'],
    'AMZN':  ['amazon', 'aws', 'prime', 'bezos', 'jassy'],
    'GOOGL': ['google', 'alphabet', 'youtube', 'gemini', 'search'],
    'NFLX':  ['netflix', 'streaming', 'subscriber'],
    'CRM':   ['salesforce', 'crm', 'marc benioff'],
    'SHOP':  ['shopify', 'e-commerce', 'merchants'],
    'ADBE':  ['adobe', 'photoshop', 'creative cloud', 'firefly'],
    'AVGO':  ['broadcom', 'vmware', 'semiconductor'],
    'COST':  ['costco', 'wholesale', 'membership'],
    'LLY':   ['lilly', 'eli lilly', 'ozempic', 'mounjaro', 'tirzepatide'],
    'QQQ':   ['nasdaq', 'tech stocks', 'qqq'],
    'SPY':   ['s&p', 'sp500', 'market', 'stocks', 'wall street'],
}


# ══════════════════════════════════════════════════════════════════════════════
class UpcomingEvent:
    """حدث اقتصادي/أرباح مجدول مسبقاً"""
    def __init__(self, symbol: str, event_type: str, scheduled_at: datetime,
                 estimate: Optional[float], previous: Optional[float],
                 pre_sentiment: str, pre_strength: float, pre_reasoning: str):
        self.symbol        = symbol
        self.event_type    = event_type      # 'earnings' | 'economic'
        self.scheduled_at  = scheduled_at
        self.estimate      = estimate        # التوقع
        self.previous      = previous        # الرقم السابق
        self.pre_sentiment = pre_sentiment   # 'positive' | 'negative' | 'neutral'
        self.pre_strength  = pre_strength    # 0.0 → 1.0
        self.pre_reasoning = pre_reasoning   # سبب التوقع

    @property
    def minutes_until(self) -> int:
        now = datetime.now(timezone.utc)
        target = self.scheduled_at
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return max(0, int((target - now).total_seconds() / 60))

    def to_signal(self) -> str:
        mins = self.minutes_until
        if mins > 60:
            timing = f"بعد {mins // 60}س {mins % 60}د"
        else:
            timing = f"بعد {mins}د"

        arrow = "🟢" if self.pre_sentiment == 'positive' else ("🔴" if self.pre_sentiment == 'negative' else "🟡")
        bars  = "█" * int(self.pre_strength * 5)
        return (
            f"⏰ [{self.symbol}] {self.event_type} — {timing}\n"
            f"   {arrow} توقع مسبق: {self.pre_sentiment.upper()} | قوة: {bars}\n"
            f"   📊 {self.pre_reasoning}"
        )

    def should_block_trading(self) -> tuple[bool, str]:
        """هل يجب إيقاف التداول قبل الحدث؟"""
        mins = self.minutes_until
        if mins <= 0 or mins > 240:
            return False, ""
        if mins <= 30:
            return True, f"⛔ {self.symbol}: {self.event_type} بعد {mins}د — تداول موقوف"
        if mins <= 120 and self.pre_strength >= 0.7:
            return True, f"⚠️ {self.symbol}: {self.event_type} بعد {mins}د — خطر عالٍ"
        return False, ""


class NewsSentiment:
    """نتيجة تحليل خبر صادر"""
    def __init__(self, symbol: str, headline: str, sentiment: str,
                 strength: float, impact: str, source: str, age_min: int):
        self.symbol    = symbol
        self.headline  = headline
        self.sentiment = sentiment
        self.strength  = strength
        self.impact    = impact
        self.source    = source
        self.age_min   = age_min

    def to_signal(self) -> str:
        arrow = "🟢" if self.sentiment == 'positive' else ("🔴" if self.sentiment == 'negative' else "🟡")
        bars  = "█" * int(self.strength * 5)
        return (f"{arrow} [{self.symbol}] {self.sentiment.upper()} "
                f"| تأثير: {self.impact} | قوة: {bars} "
                f"| منذ {self.age_min}د\n   📰 {self.headline[:80]}")


# ══════════════════════════════════════════════════════════════════════════════
class NewsAnalyzer:
    """
    محلل الأخبار الكامل:
    1. أخبار فورية من Yahoo Finance RSS (كل 5 دقائق)
    2. تقويم الأرباح من Yahoo Finance (كل ساعة)
    3. تحليل التوقعات مقابل السابق → نتيجة مسبقة
    """
    NEWS_INTERVAL     = 300   # 5 دقائق
    CALENDAR_INTERVAL = 3600  # ساعة

    def __init__(self):
        self._lock              = threading.Lock()
        self._sentiments:  Dict[str, NewsSentiment]  = {}
        self._events:      Dict[str, UpcomingEvent]  = {}
        self._thread_news: Optional[threading.Thread] = None
        self._thread_cal:  Optional[threading.Thread] = None
        self._running      = False
        self._callbacks    = []

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread_news = threading.Thread(target=self._news_loop,     daemon=True, name="NewsLoop")
        self._thread_cal  = threading.Thread(target=self._calendar_loop, daemon=True, name="CalendarLoop")
        self._thread_news.start()
        self._thread_cal.start()
        log.info("NewsAnalyzer started (news + calendar)")

    def stop(self):
        self._running = False

    def add_callback(self, fn):
        self._callbacks.append(fn)

    def _notify(self, msg: str):
        for fn in self._callbacks:
            try:
                fn(msg) if callable(fn) else None
            except Exception:
                pass

    # ── واجهة عامة ───────────────────────────────────────────────────────────
    def get_sentiment(self, symbol: str) -> Optional[NewsSentiment]:
        with self._lock:
            return self._sentiments.get(symbol)

    def get_event(self, symbol: str) -> Optional[UpcomingEvent]:
        with self._lock:
            return self._events.get(symbol)

    def get_all_events(self) -> Dict[str, UpcomingEvent]:
        with self._lock:
            return {k: v for k, v in self._events.items() if 0 < v.minutes_until <= 480}

    def news_filter(self, symbol: str, direction: str) -> tuple[bool, str]:
        """
        فلتر مزدوج: أخبار فورية + أحداث مجدولة
        يُعيد (allow, reason)
        """
        # 1. فحص الأحداث المجدولة أولاً (أولوية أعلى)
        ev = self.get_event(symbol)
        if ev and 0 < ev.minutes_until <= 240:
            block, reason = ev.should_block_trading()
            if block:
                return False, reason

            # إذا التوقع المسبق يتعارض مع الاتجاه
            if direction == 'long' and ev.pre_sentiment == 'negative' and ev.pre_strength >= 0.65:
                return False, f"❌ توقع مسبق سلبي يمنع LONG | {ev.pre_reasoning[:50]}"
            if direction == 'short' and ev.pre_sentiment == 'positive' and ev.pre_strength >= 0.65:
                return False, f"❌ توقع مسبق إيجابي يمنع SHORT | {ev.pre_reasoning[:50]}"

        # 2. فحص الأخبار الفورية
        s = self.get_sentiment(symbol)
        if s and s.age_min <= 120 and s.impact != 'low':
            if direction == 'long' and s.sentiment == 'negative' and s.strength >= 0.6:
                return False, f"❌ خبر سلبي يمنع LONG | {s.headline[:50]}"
            if direction == 'short' and s.sentiment == 'positive' and s.strength >= 0.6:
                return False, f"❌ خبر إيجابي يمنع SHORT | {s.headline[:50]}"
            if direction == 'long' and s.sentiment == 'positive':
                return True, f"✅ خبر إيجابي يدعم LONG"
            if direction == 'short' and s.sentiment == 'negative':
                return True, f"✅ خبر سلبي يدعم SHORT"

        # دعم التوقع المسبق
        if ev and 0 < ev.minutes_until <= 480:
            if direction == 'long' and ev.pre_sentiment == 'positive':
                return True, f"✅ توقع مسبق إيجابي يدعم LONG ({ev.minutes_until}د)"
            if direction == 'short' and ev.pre_sentiment == 'negative':
                return True, f"✅ توقع مسبق سلبي يدعم SHORT ({ev.minutes_until}د)"

        return True, "لا أخبار مؤثرة"

    def confidence_boost(self, symbol: str, direction: str) -> float:
        boost = 0.0
        ev = self.get_event(symbol)
        if ev and 0 < ev.minutes_until <= 480:
            b = ev.pre_strength * 0.08
            if direction == 'long':
                boost += b if ev.pre_sentiment == 'positive' else (-b if ev.pre_sentiment == 'negative' else 0)
            else:
                boost += b if ev.pre_sentiment == 'negative' else (-b if ev.pre_sentiment == 'positive' else 0)

        s = self.get_sentiment(symbol)
        if s and s.age_min <= 120 and s.impact != 'low':
            b = s.strength * 0.06
            if direction == 'long':
                boost += b if s.sentiment == 'positive' else (-b if s.sentiment == 'negative' else 0)
            else:
                boost += b if s.sentiment == 'negative' else (-b if s.sentiment == 'positive' else 0)

        return round(max(-0.15, min(0.15, boost)), 3)

    # ── حلقة الأخبار الفورية ─────────────────────────────────────────────────
    def _news_loop(self):
        self._fetch_all_news()
        while self._running:
            time.sleep(self.NEWS_INTERVAL)
            if self._running:
                self._fetch_all_news()

    def _fetch_all_news(self):
        for sym in WATCHED_SYMBOLS:
            if not self._running:
                break
            try:
                result = self._fetch_symbol_news(sym)
                if result:
                    with self._lock:
                        old = self._sentiments.get(sym)
                        self._sentiments[sym] = result
                    if old is None or old.headline != result.headline:
                        self._notify(result.to_signal())
            except Exception as e:
                log.debug(f"News error {sym}: {e}")
            time.sleep(1.5)

    def _fetch_symbol_news(self, symbol: str) -> Optional[NewsSentiment]:
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
        try:
            r = requests.get(url, timeout=10, verify=False,
                             headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                return None
            return self._parse_rss(symbol, r.text)
        except Exception as e:
            log.debug(f"RSS {symbol}: {e}")
            return None

    def _parse_rss(self, symbol: str, xml: str) -> Optional[NewsSentiment]:
        titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', xml)
        dates  = re.findall(r'<pubDate>(.*?)</pubDate>', xml)
        headlines = [t for t in titles if len(t) > 20]
        if not headlines:
            headlines = titles[1:] if len(titles) > 1 else []
        if not headlines:
            return None
        headline = headlines[0]
        age_min  = self._calc_age(dates[1] if len(dates) > 1 else "")
        if age_min > 240:
            return None
        sentiment, strength = self._analyze_sentiment(headline, symbol)
        impact = self._calc_impact(headline)
        return NewsSentiment(symbol, headline, sentiment, strength, impact, "Yahoo Finance", age_min)

    # ── حلقة التقويم ─────────────────────────────────────────────────────────
    def _calendar_loop(self):
        time.sleep(5)  # انتظر قليلاً قبل أول جلب
        self._fetch_earnings_calendar()
        while self._running:
            time.sleep(self.CALENDAR_INTERVAL)
            if self._running:
                self._fetch_earnings_calendar()

    def _fetch_earnings_calendar(self):
        """يجلب تقويم الأرباح من Yahoo Finance لكل رمز"""
        for sym in WATCHED_SYMBOLS:
            if not self._running:
                break
            try:
                ev = self._fetch_earnings(sym)
                if ev:
                    with self._lock:
                        old = self._events.get(sym)
                        self._events[sym] = ev
                    # أشعر فقط إذا الحدث خلال 8 ساعات
                    if ev.minutes_until <= 480:
                        if old is None or abs(old.minutes_until - ev.minutes_until) > 30:
                            self._notify(ev.to_signal())
            except Exception as e:
                log.debug(f"Calendar error {sym}: {e}")
            time.sleep(2)

    def _fetch_earnings(self, symbol: str) -> Optional[UpcomingEvent]:
        """يجلب بيانات الأرباح القادمة من Yahoo Finance"""
        url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}?modules=calendarEvents,defaultKeyStatistics,financialData"
        try:
            r = requests.get(url, timeout=12, verify=False,
                             headers={"User-Agent": "Mozilla/5.0",
                                      "Accept": "application/json"})
            if r.status_code != 200:
                return None
            data = r.json()
            result = data.get("quoteSummary", {}).get("result", [])
            if not result:
                return None
            d = result[0]

            # ── تاريخ الأرباح القادم ──────────────────────────────────
            cal = d.get("calendarEvents", {})
            earnings_dates = cal.get("earnings", {}).get("earningsDate", [])
            if not earnings_dates:
                return None

            next_date_raw = earnings_dates[0].get("raw")
            if not next_date_raw:
                return None

            next_dt = datetime.fromtimestamp(next_date_raw, tz=timezone.utc)
            now     = datetime.now(timezone.utc)
            mins    = int((next_dt - now).total_seconds() / 60)

            # إذا الأرباح مضت أو بعيدة جداً (أكثر من 30 يوم)
            if mins < -60 or mins > 43200:
                return None

            # ── التوقعات مقابل السابق ────────────────────────────────
            fin  = d.get("financialData", {})
            kstat = d.get("defaultKeyStatistics", {})

            # EPS
            eps_est  = (cal.get("earnings", {}).get("earningsAverage", {}) or {}).get("raw")
            eps_prev = kstat.get("trailingEps", {}).get("raw") if isinstance(kstat.get("trailingEps"), dict) else None

            # Revenue
            rev_est  = fin.get("revenueEstimates", {})
            rev_avg  = None
            if isinstance(rev_est, dict):
                rev_avg = rev_est.get("avg", {}).get("raw") if isinstance(rev_est.get("avg"), dict) else None

            # تحليل التوقع مقابل السابق
            pre_sentiment, pre_strength, reasoning = self._analyze_forecast(
                eps_estimate=eps_est,
                eps_previous=eps_prev,
                symbol=symbol,
            )

            return UpcomingEvent(
                symbol       = symbol,
                event_type   = "أرباح",
                scheduled_at = next_dt,
                estimate     = eps_est,
                previous     = eps_prev,
                pre_sentiment= pre_sentiment,
                pre_strength = pre_strength,
                pre_reasoning= reasoning,
            )
        except Exception as e:
            log.debug(f"Earnings {symbol}: {e}")
            return None

    def _analyze_forecast(self, eps_estimate: Optional[float],
                          eps_previous: Optional[float],
                          symbol: str) -> tuple[str, float, str]:
        """
        يقارن التوقع بالسابق ويخرج بتوقع مسبق
        """
        if eps_estimate is None or eps_previous is None:
            return 'neutral', 0.3, "بيانات غير كافية للتحليل"

        if eps_previous == 0:
            return 'neutral', 0.3, "EPS السابق = صفر"

        change_pct = ((eps_estimate - eps_previous) / abs(eps_previous)) * 100

        if change_pct >= 30:
            return 'positive', 0.90, f"EPS متوقع ${eps_estimate:.2f} vs سابق ${eps_previous:.2f} (+{change_pct:.0f}%) — نمو قوي جداً"
        elif change_pct >= 15:
            return 'positive', 0.75, f"EPS متوقع ${eps_estimate:.2f} vs سابق ${eps_previous:.2f} (+{change_pct:.0f}%) — نمو جيد"
        elif change_pct >= 5:
            return 'positive', 0.55, f"EPS متوقع ${eps_estimate:.2f} vs سابق ${eps_previous:.2f} (+{change_pct:.0f}%) — تحسن معتدل"
        elif change_pct >= -5:
            return 'neutral', 0.35, f"EPS متوقع ${eps_estimate:.2f} vs سابق ${eps_previous:.2f} ({change_pct:+.0f}%) — مستقر"
        elif change_pct >= -15:
            return 'negative', 0.55, f"EPS متوقع ${eps_estimate:.2f} vs سابق ${eps_previous:.2f} ({change_pct:.0f}%) — تراجع معتدل"
        elif change_pct >= -30:
            return 'negative', 0.75, f"EPS متوقع ${eps_estimate:.2f} vs سابق ${eps_previous:.2f} ({change_pct:.0f}%) — تراجع قوي"
        else:
            return 'negative', 0.90, f"EPS متوقع ${eps_estimate:.2f} vs سابق ${eps_previous:.2f} ({change_pct:.0f}%) — انهيار في الأرباح"

    # ── تحليل المشاعر ────────────────────────────────────────────────────────
    def _analyze_sentiment(self, text: str, symbol: str) -> tuple[str, float]:
        text_lower = text.lower()
        pos = sum(1 for w in POSITIVE_WORDS if w in text_lower)
        neg = sum(1 for w in NEGATIVE_WORDS if w in text_lower)
        keywords = SYMBOL_KEYWORDS.get(symbol, [])
        if any(k in text_lower for k in keywords):
            pos = int(pos * 1.3)
            neg = int(neg * 1.3)
        total = pos + neg
        if total == 0:
            return 'neutral', 0.3
        if pos > neg:
            return 'positive', round(min(pos / total, 1.0), 2)
        elif neg > pos:
            return 'negative', round(min(neg / total, 1.0), 2)
        return 'neutral', 0.3

    def _calc_impact(self, text: str) -> str:
        hits = sum(1 for w in HIGH_IMPACT_WORDS if w in text.lower())
        return 'high' if hits >= 2 else ('medium' if hits == 1 else 'low')

    def _calc_age(self, date_str: str) -> int:
        try:
            from email.utils import parsedate_to_datetime
            date_str = re.sub(r'\s+\w{3}$', ' +0000', date_str.strip())
            pub = parsedate_to_datetime(date_str)
            now = datetime.now(timezone.utc)
            return int((now - pub).total_seconds() / 60)
        except Exception:
            return 999


# ── Singleton ─────────────────────────────────────────────────────────────────
_instance: Optional[NewsAnalyzer] = None

def get_news_analyzer() -> NewsAnalyzer:
    global _instance
    if _instance is None:
        _instance = NewsAnalyzer()
        _instance.start()
    elif not _instance._running:
        _instance.start()
    return _instance

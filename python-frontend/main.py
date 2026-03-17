
import os
import sys
import json
import math
import threading
import subprocess
import time
from datetime import datetime
import tempfile


os.environ.setdefault("KIVY_NO_ENV_CONFIG", "1")

import kivy
kivy.require("2.0.0")

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.lang import Builder
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.widget import Widget
from kivy.properties import (
    StringProperty, NumericProperty, BooleanProperty,
    ListProperty, ObjectProperty
)
from kivy.graphics import (
    Color, Rectangle, RoundedRectangle, Line, Ellipse,
    InstructionGroup
)
from kivy.metrics import dp, sp
from kivy.animation import Animation

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

RUST_BIN    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rust-backend", "target", "release", "weather")
SERVER_URL  = "http://127.0.0.1:8765/weather"
JSON_PATH   = os.path.join(tempfile.gettempdir(), "hattiesburg_weather.json")
REFRESH_SEC = 300  


def font(name):
    mapping = {
        "FiraCode-Regular.ttf":    "DejaVuSans",
        "FiraCode-Bold.ttf":       "DejaVuSans",
        "Merriweather-Bold.ttf":   "DejaVuSans",
        "DejaVuSerif-Regular.ttf": "DejaVuSans",
    }
    return mapping.get(name, "DejaVuSans")


#Mock data (shown while real data loads / on network failure)
MOCK_DATA = {
    "current": {
        "temp_f": 88.0,
        "feels_like_f": 97.0,
        "humidity_pct": 74.0,
        "wind_mph": 9.0,
        "wind_dir": "SSW",
        "condition": "Partly Cloudy",
        "heat_warning": True,
    },
    "hourly": [
        {"hour": f"{h}{'PM' if h >= 12 else 'AM'}", "temp_f": 85 + h % 8, "condition": "Mostly Sunny", "precip_pct": 10}
        for h in range(1, 13)
    ],
    "daily": [
        {"day": d, "high_f": hi, "low_f": lo, "precip_pct": pp, "summary": s, "is_rainy": pp >= 40}
        for d, hi, lo, pp, s in [
            ("Today",    91, 72, 40, "Chance of Thunderstorms"),
            ("Tuesday",  89, 71, 20, "Partly Cloudy"),
            ("Wednesday",87, 70, 60, "Showers Likely"),
            ("Thursday", 85, 69, 30, "Mostly Cloudy"),
            ("Friday",   90, 73, 10, "Mostly Sunny"),
            ("Saturday", 93, 75, 5,  "Sunny"),
            ("Sunday",   88, 72, 50, "Isolated Storms"),
        ]
    ],
    "alerts": ["Heat Advisory in effect until 7 PM CDT"],
    "last_updated": datetime.now().strftime("%b %d, %Y %I:%M %p"),
}


#Condition → emoji icon mapping 
def condition_icon(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ("thunder", "storm", "lightning")):  return "⛈"
    if any(w in t for w in ("rain", "shower", "drizzle")):      return "🌧"
    if any(w in t for w in ("snow", "sleet", "flurr")):         return "❄"
    if any(w in t for w in ("fog", "mist", "haze")):            return "🌫"
    if any(w in t for w in ("partly", "mostly cloud")):         return "⛅"
    if any(w in t for w in ("cloud", "overcast")):              return "☁"
    if any(w in t for w in ("sunny", "clear")):                 return "☀"
    return "🌤"


def precip_color(pct: float):
    """Return RGBA for a precipitation probability."""
    if pct >= 70:   return (0.25, 0.55, 0.95, 1)
    if pct >= 40:   return (0.40, 0.70, 0.90, 1)
    if pct >= 20:   return (0.55, 0.75, 0.85, 0.8)
    return (0.35, 0.45, 0.60, 0.5)

#custom widgets
class GlassCard(BoxLayout):
    """A rounded card with a semi-transparent dark background."""
    radius = NumericProperty(14)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.bind(pos=self._redraw, size=self._redraw)

    def _redraw(self, *_):
        self.canvas.before.clear()
        with self.canvas.before:
            Color(0.12, 0.15, 0.22, 0.95)
            RoundedRectangle(pos=self.pos, size=self.size,
                             radius=[dp(self.radius)])
            Color(0.30, 0.40, 0.55, 0.15)
            Line(rounded_rectangle=[self.x, self.y, self.width, self.height,
                                    dp(self.radius)], width=1)


class AlertBanner(BoxLayout):
    """Pulsing red alert banner."""
    
    def __init__(self, **kw):
        super().__init__(**kw)
        self.size_hint_y = None
        self.height = dp(44)
        self.padding = [dp(16), 0]
        self._alpha = 1.0
        self.bind(pos=self._redraw, size=self._redraw)
        self._pulse()

    def _redraw(self, *_):
        self.canvas.before.clear()
        with self.canvas.before:
            Color(0.72, 0.15, 0.15, self._alpha)
            RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(8)])

    def _pulse(self, *_):
        anim = (Animation(_alpha=0.6, duration=0.9) +
                Animation(_alpha=1.0, duration=0.9))
        anim.bind(on_progress=lambda *a: self._redraw())
        anim.repeat = True
        anim.start(self)


class HeatBadge(BoxLayout):
    """Amber heat-warning pill badge."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.size_hint = (None, None)
        self.size = (dp(280), dp(34))
        self.padding = [dp(14), 0]
        self.bind(pos=self._redraw, size=self._redraw)
        lbl = Label(
            text="🌡  Mississippi Heat Warning",
            font_name=font("FiraCode-Bold.ttf"),
            font_size=sp(12),
            color=(0.10, 0.06, 0.00, 1),
            bold=True,
        )
        self.add_widget(lbl)

    def _redraw(self, *_):
        self.canvas.before.clear()
        with self.canvas.before:
            Color(0.94, 0.65, 0.00, 1)
            RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(17)])


class StatCard(GlassCard):
    """Small icon+value+label stat tile."""
    def __init__(self, icon, value, label, **kw):
        super().__init__(orientation="vertical", padding=dp(10),
                         spacing=dp(2), **kw)
        self.size_hint_y = None
        self.height = dp(88)
        self.add_widget(Label(text=icon, font_size=sp(22), size_hint_y=None,
                              height=dp(30)))
        self.add_widget(Label(text=value,
                              font_name=font("Merriweather-Bold.ttf"),
                              font_size=sp(16), bold=True,
                              color=(0.95, 0.85, 0.55, 1),
                              size_hint_y=None, height=dp(24)))
        self.add_widget(Label(text=label,
                              font_name=font("FiraCode-Regular.ttf"),
                              font_size=sp(10),
                              color=(0.50, 0.58, 0.72, 1)))


class DayCard(GlassCard):
    """One card in the 7-day forecast strip."""
    def __init__(self, day_data, **kw):
        super().__init__(orientation="vertical", padding=dp(8),
                         spacing=dp(3), **kw)
        self.size_hint_x = None
        self.width  = dp(108)
        self.size_hint_y = None
        self.height = dp(148)

        d = day_data
        icon = condition_icon(d.get("summary", ""))
        rain_pct = d.get("precip_pct", 0)
        rain_col = precip_color(rain_pct)

        # Day name
        self.add_widget(Label(
            text=d.get("day", "")[:3],
            font_name=font("FiraCode-Bold.ttf"),
            font_size=sp(11),
            color=(0.55, 0.62, 0.78, 1),
            size_hint_y=None, height=dp(18),
        ))
        # Weather icon
        self.add_widget(Label(text=icon, font_size=sp(26),
                              size_hint_y=None, height=dp(34)))
        # High / Low
        self.add_widget(Label(
            text=f"{d.get('high_f', 0):.0f}° / {d.get('low_f', 0):.0f}°",
            font_name=font("Merriweather-Bold.ttf"),
            font_size=sp(12), bold=True,
            color=(0.95, 0.88, 0.68, 1),
            size_hint_y=None, height=dp(20),
        ))
        # Precip bar background
        bar_bg = Widget(size_hint_y=None, height=dp(8))
        bar_bg.bind(pos=lambda w, _: self._draw_bar(w, rain_pct, rain_col),
                    size=lambda w, _: self._draw_bar(w, rain_pct, rain_col))
        self._bar_bg = bar_bg
        self._bar_data = (rain_pct, rain_col)
        self.add_widget(bar_bg)
        # Precip %
        self.add_widget(Label(
            text=f"💧 {rain_pct:.0f}%",
            font_name=font("FiraCode-Regular.ttf"),
            font_size=sp(10),
            color=(*rain_col[:3], 1),
            size_hint_y=None, height=dp(16),
        ))
        # Short summary (truncated)
        summary = d.get("summary", "")
        if len(summary) > 14:
            summary = summary[:13] + "…"
        self.add_widget(Label(
            text=summary,
            font_name=font("FiraCode-Regular.ttf"),
            font_size=sp(9),
            color=(0.45, 0.52, 0.65, 1),
        ))

    def _draw_bar(self, widget, pct, col):
        widget.canvas.clear()
        with widget.canvas:
            Color(0.20, 0.25, 0.35, 1)
            RoundedRectangle(pos=widget.pos, size=widget.size, radius=[dp(4)])
            Color(*col)
            fill_w = max(dp(4), widget.width * (pct / 100.0))
            RoundedRectangle(pos=widget.pos,
                             size=(fill_w, widget.height),
                             radius=[dp(4)])


class HourlyChart(Widget):
    """
    Canvas-drawn line chart of hourly temperatures + precip bars.
    """
    hourly_data = ListProperty([])

    def __init__(self, **kw):
        super().__init__(**kw)
        self.size_hint_y = None
        self.height = dp(140)
        self.bind(hourly_data=self._redraw, pos=self._redraw, size=self._redraw)

    def _redraw(self, *_):
        self.canvas.clear()
        data = self.hourly_data
        if not data:
            return

        pad_l, pad_r = dp(32), dp(16)
        pad_t, pad_b = dp(16), dp(36)
        W = self.width  - pad_l - pad_r
        H = self.height - pad_t - pad_b

        temps  = [d["temp_f"]   for d in data]
        precip = [d["precip_pct"] for d in data]
        t_min, t_max = min(temps), max(temps)
        t_range = max(t_max - t_min, 1)

        n = len(data)
        step = W / max(n - 1, 1)

        def tx(i): return self.x + pad_l + i * step
        def ty(v): return self.y + pad_b + ((v - t_min) / t_range) * H

        with self.canvas:
            # ── Grid lines 
            Color(0.22, 0.28, 0.38, 0.6)
            for k in range(4):
                yy = self.y + pad_b + (k / 3) * H
                Line(points=[self.x + pad_l, yy,
                              self.x + pad_l + W, yy], width=0.8)

            # ── Precip bars (background) 
            bar_w = max(step * 0.5, dp(6))
            for i, d in enumerate(data):
                pct = d["precip_pct"]
                if pct > 0:
                    bh = (pct / 100.0) * H * 0.6
                    col = precip_color(pct)
                    Color(*col[:3], 0.35)
                    Rectangle(pos=(tx(i) - bar_w / 2,
                                   self.y + pad_b),
                              size=(bar_w, bh))

            #Temperature line (glow + solid) 
            pts = []
            for i, t in enumerate(temps):
                pts += [tx(i), ty(t)]

            Color(0.94, 0.65, 0.00, 0.18)
            Line(points=pts, width=dp(4))
            Color(0.94, 0.65, 0.00, 1)
            Line(points=pts, width=dp(1.8))

            # Dots at each hour 
            for i, t in enumerate(temps):
                Color(0.12, 0.15, 0.22, 1)
                Ellipse(pos=(tx(i) - dp(4), ty(t) - dp(4)),
                        size=(dp(8), dp(8)))
                Color(0.94, 0.65, 0.00, 1)
                Ellipse(pos=(tx(i) - dp(3), ty(t) - dp(3)),
                        size=(dp(6), dp(6)))

            #  Temp labels (every other point) 
            # (drawn via canvas Label-equivalent — use kivy Label overlaid)

        # Remove old overlay labels
        for child in list(self.children):
            self.remove_widget(child)

        for i, d in enumerate(data):
            if i % 2 == 0:
                # Temp
                lbl = Label(
                    text=f"{d['temp_f']:.0f}°",
                    font_name=font("FiraCode-Regular.ttf"),
                    font_size=sp(9),
                    color=(0.90, 0.80, 0.50, 1),
                    size_hint=(None, None),
                    size=(dp(32), dp(14)),
                )
                lbl.pos = (tx(i) - dp(16), ty(d["temp_f"]) + dp(5))
                self.add_widget(lbl)
                # Hour label
                hour_lbl = Label(
                    text=data[i]["hour"][:5],
                    font_name=font("FiraCode-Regular.ttf"),
                    font_size=sp(8),
                    color=(0.45, 0.52, 0.65, 1),
                    size_hint=(None, None),
                    size=(dp(40), dp(14)),
                )
                hour_lbl.pos = (tx(i) - dp(20), self.y + dp(4))
                self.add_widget(hour_lbl)


#  Root Layout

class WeatherRoot(FloatLayout):
    """Top-level layout — dark background + scrollable content."""

    def __init__(self, **kw):
        super().__init__(**kw)
        Window.clearcolor = (0.08, 0.10, 0.15, 1)

        self._data = None
        self._loading = True

        # Main vertical scroll
        self._scroll = ScrollView(
            size_hint=(1, 1),
            bar_width=dp(4),
            bar_color=(0.94, 0.65, 0.00, 0.6),
        )
        self._content = BoxLayout(
            orientation="vertical",
            padding=[dp(16), dp(16), dp(16), dp(24)],
            spacing=dp(12),
            size_hint_y=None,
        )
        self._content.bind(minimum_height=self._content.setter("height"))
        self._scroll.add_widget(self._content)
        self.add_widget(self._scroll)

        # Refresh FAB (bottom-right)
        self._fab = Button(
            text="↻",
            font_size=sp(22),
            size_hint=(None, None),
            size=(dp(52), dp(52)),
            background_color=(0, 0, 0, 0),
            pos_hint={"right": 0.97, "y": 0.02},
        )
        self._fab.bind(on_release=lambda *_: self.refresh())
        self._fab.bind(pos=self._update_fab_bg, size=self._update_fab_bg)
        self._fab.color = (0.10, 0.06, 0.00, 1)
        self.add_widget(self._fab)

        # Load data
        self.render(MOCK_DATA, loading=True)
        Clock.schedule_once(lambda dt: self.refresh(), 0.3)

    def _update_fab_bg(self, *_):
        self._fab.canvas.before.clear()
        with self._fab.canvas.before:
            Color(0.94, 0.65, 0.00, 1)
            self._fab_bg = Ellipse(pos=self._fab.pos, size=self._fab.size)

    # Data loading 

    def refresh(self):
        self._fab.text = "…"
        threading.Thread(target=self._fetch_thread, daemon=True).start()

    def _fetch_thread(self):
        data = None
        # 1. Try HTTP server
        if HAS_REQUESTS:
            try:
                resp = _requests.get(SERVER_URL, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
            except Exception:
                pass

        # 2. Try JSON file fallback
        if data is None:
            try:
                with open(JSON_PATH) as f:
                    data = json.load(f)
            except Exception:
                pass

        # 3. Try spawning Rust binary directly
        if data is None:
            rust = os.path.abspath(RUST_BIN)
            if os.path.isfile(rust):
                try:
                    result = subprocess.run(
                        [rust], capture_output=True, text=True, timeout=30
                    )
                    data = json.loads(result.stdout)
                except Exception:
                    pass

        Clock.schedule_once(
            lambda dt: self.render(data or MOCK_DATA, loading=False)
        )

    # Rendering 

    def render(self, data: dict, loading=False):
        self._data = data
        self._loading = loading
        c = self._content
        c.clear_widgets()

        cur  = data.get("current", {})
        hour = data.get("hourly",  [])
        days = data.get("daily",   [])
        alts = data.get("alerts",  [])
        upd  = data.get("last_updated", "")

        # Alerts 
        for alert_text in alts[:2]:
            ab = AlertBanner()
            lbl = Label(
                text=f"⚠  {alert_text}",
                font_name=font("FiraCode-Regular.ttf"),
                font_size=sp(11),
                color=(1, 0.88, 0.88, 1),
                text_size=(Window.width - dp(64), None),
                halign="center",
                valign="middle",
            )
            ab.add_widget(lbl)
            c.add_widget(ab)

        #Header 
        header = BoxLayout(orientation="vertical", size_hint_y=None,
                           height=dp(48), spacing=0)
        header.add_widget(Label(
            text="Hattiesburg, Mississippi",
            font_name=font("Merriweather-Bold.ttf"),
            font_size=sp(18),
            bold=True,
            color=(0.92, 0.85, 0.65, 1),
            halign="center",
            valign="bottom",
            text_size=(Window.width, None),
        ))
        ts_text = "Loading…" if loading else f"Updated {upd}"
        header.add_widget(Label(
            text=ts_text,
            font_name=font("FiraCode-Regular.ttf"),
            font_size=sp(10),
            color=(0.42, 0.50, 0.65, 1),
            halign="center",
            text_size=(Window.width, None),
        ))
        c.add_widget(header)

        # Heat badge 
        if cur.get("heat_warning"):
            badge_wrap = BoxLayout(size_hint_y=None, height=dp(40))
            badge = HeatBadge()
            badge_wrap.add_widget(Widget())
            badge_wrap.add_widget(badge)
            badge_wrap.add_widget(Widget())
            c.add_widget(badge_wrap)

        # Current conditions card 
        curr_card = GlassCard(orientation="vertical", radius=18,
                              size_hint_y=None, height=dp(220),
                              padding=[dp(20), dp(16)], spacing=dp(4))
        icon = condition_icon(cur.get("condition", ""))

        # Icon + temp row
        icon_temp = BoxLayout(orientation="horizontal", size_hint_y=None,
                              height=dp(110))
        icon_temp.add_widget(Label(
            text=icon, font_size=sp(72), size_hint_x=0.38,
        ))
        temp_col = BoxLayout(orientation="vertical")
        temp_col.add_widget(Label(
            text=f"{cur.get('temp_f', 0):.1f}°F",
            font_name=font("Merriweather-Bold.ttf"),
            font_size=sp(48),
            bold=True,
            color=(0.97, 0.90, 0.65, 1),
            halign="left",
            valign="bottom",
            text_size=(dp(240), None),
        ))
        temp_col.add_widget(Label(
            text=f"Feels like {cur.get('feels_like_f', 0):.1f}°F",
            font_name=font("FiraCode-Regular.ttf"),
            font_size=sp(13),
            color=(0.60, 0.70, 0.85, 1),
            halign="left",
            text_size=(dp(240), None),
        ))
        icon_temp.add_widget(temp_col)
        curr_card.add_widget(icon_temp)

        curr_card.add_widget(Label(
            text=cur.get("condition", "Unknown"),
            font_name=font("DejaVuSerif-Regular.ttf"),
            font_size=sp(14),
            color=(0.80, 0.72, 0.55, 1),
            halign="center",
            text_size=(Window.width - dp(72), None),
            size_hint_y=None,
            height=dp(22),
        ))

        # Wind row
        curr_card.add_widget(Label(
            text=f"💨  {cur.get('wind_mph', 0):.1f} mph  {cur.get('wind_dir', '--')}   💧  {cur.get('humidity_pct', 0):.0f}%",
            font_name=font("FiraCode-Regular.ttf"),
            font_size=sp(12),
            color=(0.50, 0.58, 0.72, 1),
            halign="center",
            text_size=(Window.width - dp(72), None),
            size_hint_y=None,
            height=dp(22),
        ))
        c.add_widget(curr_card)

        # Stats row 
        stats_row = BoxLayout(orientation="horizontal", spacing=dp(8),
                              size_hint_y=None, height=dp(88))
        stats = [
            ("🌡", f"{cur.get('temp_f', 0):.0f}°F",    "Temperature"),
            ("🤔", f"{cur.get('feels_like_f', 0):.0f}°F","Feels Like"),
            ("💧", f"{cur.get('humidity_pct', 0):.0f}%", "Humidity"),
            ("💨", f"{cur.get('wind_mph', 0):.0f} mph",  "Wind"),
        ]
        for icon_s, val, lbl in stats:
            stats_row.add_widget(StatCard(icon_s, val, lbl))
        c.add_widget(stats_row)

        # 7-day forecast 
        c.add_widget(self._section_label("7-DAY FORECAST"))
        scroll_7 = ScrollView(
            size_hint_y=None,
            height=dp(158),
            do_scroll_y=False,
            bar_width=dp(3),
            bar_color=(0.94, 0.65, 0.00, 0.4),
        )
        days_row = BoxLayout(orientation="horizontal", spacing=dp(8),
                             size_hint_x=None, padding=[dp(2), dp(4)])
        days_row.bind(minimum_width=days_row.setter("width"))
        for d in days:
            days_row.add_widget(DayCard(d))
        scroll_7.add_widget(days_row)
        c.add_widget(scroll_7)

        # Hourly temp chart 
        if hour:
            c.add_widget(self._section_label("NEXT 12 HOURS"))
            chart_card = GlassCard(orientation="vertical", radius=14,
                                   size_hint_y=None, height=dp(170),
                                   padding=[dp(8), dp(8)])
            chart = HourlyChart()
            chart.hourly_data = hour[:12]
            chart_card.add_widget(chart)
            c.add_widget(chart_card)

        # Rain summary 
        c.add_widget(self._section_label("RAIN THIS WEEK"))
        rain_card = GlassCard(orientation="vertical", radius=14,
                              size_hint_y=None, height=dp(100),
                              padding=[dp(16), dp(8)], spacing=dp(4))
        for d in days[:4]:
            row = BoxLayout(orientation="horizontal", size_hint_y=None,
                            height=dp(18))
            row.add_widget(Label(
                text=d["day"][:3],
                font_name=font("FiraCode-Regular.ttf"),
                font_size=sp(10),
                color=(0.55, 0.62, 0.78, 1),
                size_hint_x=None, width=dp(36),
                halign="left", text_size=(dp(36), None),
            ))
            # Bar
            bar = Widget(size_hint_x=1)
            pct = d.get("precip_pct", 0)
            col = precip_color(pct)
            def make_bar_draw(w=bar, p=pct, cl=col):
                def draw(*_):
                    w.canvas.clear()
                    with w.canvas:
                        Color(0.18, 0.22, 0.30, 1)
                        RoundedRectangle(pos=w.pos, size=w.size, radius=[dp(3)])
                        Color(*cl[:3], 0.9)
                        fill = max(dp(4), w.width * (p / 100.0))
                        RoundedRectangle(pos=w.pos, size=(fill, w.height),
                                         radius=[dp(3)])
                w.bind(pos=draw, size=draw)
            make_bar_draw()
            row.add_widget(bar)
            row.add_widget(Label(
                text=f"{pct:.0f}%",
                font_name=font("FiraCode-Regular.ttf"),
                font_size=sp(10),
                color=(*col[:3], 1),
                size_hint_x=None, width=dp(34),
                halign="right", text_size=(dp(34), None),
            ))
            rain_card.add_widget(row)
        c.add_widget(rain_card)

        # Reset FAB
        self._fab.text = "↻"

    def _section_label(self, text):
        return Label(
            text=text,
            font_name=font("FiraCode-Regular.ttf"),
            font_size=sp(10),
            color=(0.42, 0.50, 0.65, 1),
            halign="left",
            text_size=(Window.width, None),
            size_hint_y=None,
            height=dp(22),
        )


#  App

class HattiesburgWeatherApp(App):
    title = "Hattiesburg Weather"

    def build(self):
        Window.size = (420, 780)
        return WeatherRoot()

    def on_start(self):
        # Start Rust backend server if binary exists
        rust = os.path.abspath(RUST_BIN)
        if os.path.isfile(rust):
            threading.Thread(
                target=lambda: subprocess.Popen(
                    [rust, "--serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ),
                daemon=True,
            ).start()
            print(f"[app] Started Rust weather server from {rust}")
        else:
            print(f"[app] Rust binary not found at {rust}, will use JSON fallback")

        # Schedule periodic refresh
        Clock.schedule_interval(
            lambda dt: self.root.refresh(), REFRESH_SEC
        )


if __name__ == "__main__":
    HattiesburgWeatherApp().run()

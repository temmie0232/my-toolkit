#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claude Code status line.

Reads a JSON payload on stdin (provided by Claude Code on every status-line
refresh) and prints two lines:

  line 1: everything else . ctx, model, cost, output style
  line 2: rate limits ..... 5h / 7d / per-model weekly percentages

Pure Python 3 standard library only (no pip deps).  Every piece is built inside
its own try/except so a missing field, a non-git dir, or a dead API never leaks
a traceback onto the status line.
"""

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #

BAR_WIDTH = 15
BLOCKS = " ▏▎▍▌▋▊▉█"  # 0/8 .. 8/8
EMPTY = "░"                                              # light shade
WRAP_WIDTH = 100
CACHE_TTL = 120        # seconds
LOCK_TTL = 60          # seconds

# colors (TrueColor RGB)
CYAN = (90, 200, 220)
GRAY = (140, 140, 140)
MAGENTA = (205, 120, 205)
GREEN = (90, 200, 110)
RED = (220, 90, 90)
YELLOW = (220, 200, 90)

ANSI_RE = re.compile(r"\033\[[0-9;]*m")

try:
    import getpass
    _USER = getpass.getuser()
except Exception:
    _USER = str(os.getuid()) if hasattr(os, "getuid") else "u"

_TMP = tempfile.gettempdir()
CACHE_PATH = os.path.join(_TMP, "claude_statusline_usage_%s.json" % _USER)
LOCK_PATH = os.path.join(_TMP, "claude_statusline_usage_%s.lock" % _USER)


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def color(text, rgb):
    # monochrome: colors disabled -> terminal default fg (white/black)
    return text


def vis_width(s):
    """visible width, ANSI escapes stripped."""
    return len(ANSI_RE.sub("", s))


def g(d, *keys, **kw):
    """safe nested get."""
    default = kw.get("default")
    cur = d
    for k in keys:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur


def _lerp(a, b, t):
    return int(round(a + (b - a) * t))


def pct_color(p):
    """0%=green -> 50%=yellow -> 100%=red, linear interpolation."""
    p = max(0.0, min(100.0, p))
    green, yellow, red = (0, 200, 80), (230, 200, 60), (230, 60, 60)
    if p <= 50:
        t = p / 50.0
        a, b = green, yellow
    else:
        t = (p - 50.0) / 50.0
        a, b = yellow, red
    return (_lerp(a[0], b[0], t), _lerp(a[1], b[1], t), _lerp(a[2], b[2], t))


def render_bar(p, width=BAR_WIDTH):
    """1/8-block precision bar so a 1% change is visible."""
    p = max(0.0, min(100.0, p))
    filled = p / 100.0 * width
    full = int(filled)
    if full >= width:
        return "█" * width
    rem = filled - full
    eighths = int(rem * 8)          # floor -> matches /usage rounding
    partial = BLOCKS[eighths] if eighths > 0 else ""
    empty = width - full - (1 if partial else 0)
    return "█" * full + partial + EMPTY * empty


# --------------------------------------------------------------------------- #
# "time until reset" formatting  ->  e.g. 1d3h12m
# --------------------------------------------------------------------------- #

def _to_epoch(resets_at):
    """Accepts unix seconds (int/float) or an ISO-8601 string -> unix seconds."""
    if isinstance(resets_at, (int, float)):
        return float(resets_at)
    s = str(resets_at).strip().replace("Z", "+00:00")
    return datetime.fromisoformat(s).timestamp()


def fmt_remaining(resets_at):
    """Time left until the window resets (e.g. 1d3h, 3h12m, 12m).

    When days are present the minutes are dropped (1d3h); otherwise show
    hours+minutes (3h12m), or just minutes (12m).
    """
    try:
        rem = int(_to_epoch(resets_at) - _now())
        if rem < 0:
            rem = 0
        total_min = rem // 60
        d = total_min // 1440
        h = (total_min % 1440) // 60
        m = total_min % 60
        if d:
            return "%dd%dh" % (d, h)          # days present -> drop minutes
        if h:
            return "%dh%dm" % (h, m)
        return "%dm" % m
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# usage segment "label: N%"  -- shared by ctx / 5h / 7d / weekly items on line 1
# --------------------------------------------------------------------------- #

def bar_segment(label, pct, reset_str):
    """One usage item: 'label: N%' (+ ' / <time-left>' when a reset is given)."""
    try:
        p = float(pct)
    except (TypeError, ValueError):
        return None
    p = max(0.0, min(100.0, p))
    seg = "%s: %d%%" % (label, int(round(p)))
    if reset_str:
        seg += " / %s" % reset_str
    return color(seg, pct_color(p))


# --------------------------------------------------------------------------- #
# weekly-limit cache (usage API) -- read here, refreshed by a detached child
# --------------------------------------------------------------------------- #

def read_token():
    """Return the OAuth access token, or None.  Never logged/printed."""
    path = os.path.expanduser(os.path.join("~", ".claude", ".credentials.json"))
    try:
        with open(path, "r", encoding="utf-8") as f:
            tok = json.load(f).get("claudeAiOauth", {}).get("accessToken")
        if tok:
            return tok
    except Exception:
        pass
    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-s",
                 "Claude Code-credentials", "-a", os.environ.get("USER", ""), "-w"],
                capture_output=True, text=True, timeout=5)
            if out.returncode == 0 and out.stdout.strip():
                tok = json.loads(out.stdout).get("claudeAiOauth", {}).get("accessToken")
                if tok:
                    return tok
        except Exception:
            pass
    return None


def refresh_usage_cache():
    """Child-process entry: fetch the usage API and write the cache.

    Writes ONLY model/percent/reset info -- never the token -- and produces no
    stdout/stderr.
    """
    tok = read_token()
    if not tok:
        return
    import urllib.request
    req = urllib.request.Request(
        "https://api.anthropic.com/api/oauth/usage",
        headers={"Authorization": "Bearer %s" % tok,
                 "anthropic-beta": "oauth-2025-04-20"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return
    bars = []
    for lim in data.get("limits", []) or []:
        if lim.get("kind") != "weekly_scoped":
            continue
        model = (lim.get("scope", {}) or {}).get("model", {}) or {}
        name = model.get("display_name")
        pct = lim.get("percent")
        if name is None or pct is None:
            continue
        bars.append({"label": name, "percent": pct,
                     "resets_at": lim.get("resets_at")})
    try:
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(bars, f)
        os.replace(tmp, CACHE_PATH)
    except Exception:
        pass


def maybe_spawn_refresh():
    """Trigger a background refresh when the cache is stale/missing.

    Never blocks the main path: the child is a fire-and-forget Popen.
    """
    try:
        now = _now()
        fresh = (os.path.exists(CACHE_PATH)
                 and now - os.path.getmtime(CACHE_PATH) < CACHE_TTL)
        if fresh:
            return
        # avoid stampede: one refresh per LOCK_TTL window
        if os.path.exists(LOCK_PATH) and now - os.path.getmtime(LOCK_PATH) < LOCK_TTL:
            return
        try:
            open(LOCK_PATH, "w").close()
        except Exception:
            pass
        kwargs = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      stdin=subprocess.DEVNULL)
        if hasattr(os, "setsid"):
            kwargs["start_new_session"] = True
        subprocess.Popen([sys.executable, os.path.abspath(__file__),
                          "--refresh-usage"], **kwargs)
    except Exception:
        pass


def read_weekly_bars():
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    out = []
    for b in data or []:
        seg = bar_segment(b.get("label", "?"), b.get("percent"),
                          fmt_remaining(b.get("resets_at")))
        if seg:
            out.append(seg)
    return out


def _now():
    # isolated so the whole thing degrades gracefully if time is unavailable
    import time
    return time.time()


# --------------------------------------------------------------------------- #
# line builders (each returns list of item strings; failures -> [])
# --------------------------------------------------------------------------- #

def build_rates(data, weekly_bars):
    """Rate-limit items: 5h / 7d / per-model weekly percentages."""
    items = []
    for key in ("five_hour", "seven_day"):
        rl = g(data, "rate_limits", key)
        if not isinstance(rl, dict):
            continue
        label = "5h" if key == "five_hour" else "7d"
        seg = bar_segment(label, rl.get("used_percentage"),
                          fmt_remaining(rl.get("resets_at")))
        if seg:
            items.append(seg)
    items.extend(weekly_bars)                 # per-model weekly limits
    return items


def build_other(data):
    """Everything else: ctx + Claude info (model, cost, output style)."""
    items = []
    # 1. context window
    ctx = g(data, "context_window", "used_percentage")
    if ctx is not None:
        seg = bar_segment("ctx", ctx, "")     # no reset for ctx
        if seg:
            items.append(seg)
    # 2. model + effort
    model = g(data, "model", "display_name")
    if model:
        seg = color(model, CYAN)
        effort = g(data, "effort", "level")
        if effort:
            seg += color(" %s" % effort, GRAY)
        items.append(seg)
    # 3. cost
    cost = g(data, "cost", "total_cost_usd", default=0) or 0
    try:
        if float(cost) > 0:
            items.append(color("$%.2f" % float(cost), GRAY))
    except (TypeError, ValueError):
        pass
    # 4. output style (hide when default)
    style = g(data, "output_style", "name")
    if style and style != "default":
        items.append(color(style, GRAY))
    return items


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #

def wrap_line2(line2_items):
    """Fill line 2 up to WRAP_WIDTH; overflow items go to the front of line 3."""
    kept, overflow = [], []
    width = 0
    sep_w = 3  # " | " visible width
    for i, it in enumerate(line2_items):
        w = vis_width(it)
        add = w + (sep_w if kept else 0)
        if kept and width + add > WRAP_WIDTH:
            overflow = line2_items[i:]
            break
        width += add
        kept.append(it)
    return kept, overflow


def main():
    sep = color(" │ ", GRAY)
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    try:
        data = json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    # kick off (non-blocking) weekly-limit refresh, then read whatever we have
    try:
        maybe_spawn_refresh()
    except Exception:
        pass
    try:
        weekly = read_weekly_bars()
    except Exception:
        weekly = []

    def safe(fn, *a):
        try:
            return fn(*a) or []
        except Exception:
            return []

    rates = safe(build_rates, data, weekly)    # 5h / 7d / weekly
    other = safe(build_other, data)            # ctx + model / cost / style

    # other on top, rate limits below
    out = []
    if other:
        out.append(sep.join(other))
    if rates:
        out.append(sep.join(rates))
    if out:
        sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if "--refresh-usage" in sys.argv:
        try:
            refresh_usage_cache()
        except Exception:
            pass
        finally:
            try:
                os.remove(LOCK_PATH)
            except Exception:
                pass
        sys.exit(0)
    try:
        main()
    except Exception:
        # worst failure mode is a traceback on the status line -> stay silent
        pass

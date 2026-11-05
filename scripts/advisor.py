#!/usr/bin/env python3
"""docs/data.json から今朝のアドバイスを計算し、docs/advice.json に保存、Webhookへ通知する。
計算ロジックは docs/index.html と同一(同じ入力なら同じ結果になる)。"""
import json, math, os, sys, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))
DEFAULT_SET = dict(capital=1000000, riskPct=0.5, maxPosPct=20, maxPositions=5, maxNew=2, cashPct=40, stopAtr=2)


# ---------- indicators ----------
def sma(a, n):
    r, s = [None] * len(a), 0.0
    for i, x in enumerate(a):
        s += x
        if i >= n:
            s -= a[i - n]
        if i >= n - 1:
            r[i] = s / n
    return r


def ema(a, n):
    k, p, r = 2 / (n + 1), None, []
    for x in a:
        p = x if p is None else x * k + p * (1 - k)
        r.append(p)
    return r


def rsi(c, n=14):
    r, g, l = [None] * len(c), 0.0, 0.0
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]
        u, w = max(d, 0), max(-d, 0)
        if i <= n:
            g += u
            l += w
            if i == n:
                g /= n
                l /= n
                r[i] = 100 if l == 0 else 100 - 100 / (1 + g / l)
        else:
            g = (g * (n - 1) + u) / n
            l = (l * (n - 1) + w) / n
            r[i] = 100 if l == 0 else 100 - 100 / (1 + g / l)
    return r


def atr(b, n=14):
    r, p, tr = [None] * len(b), None, []
    for i, x in enumerate(b):
        t = x["h"] - x["l"] if i == 0 else max(x["h"] - x["l"], abs(x["h"] - b[i - 1]["c"]), abs(x["l"] - b[i - 1]["c"]))
        tr.append(t)
        if i == n - 1:
            p = sum(tr) / n
            r[i] = p
        elif i >= n:
            p = (p * (n - 1) + t) / n
            r[i] = p
    return r


def analyze(rows):
    if not rows or len(rows) < 80:
        return None
    bars = [dict(d=x[0], o=x[1], h=x[2], l=x[3], c=x[4], v=x[5]) for x in rows]
    c = [x["c"] for x in bars]
    v = [x["v"] for x in bars]
    i = len(c) - 1
    s25, s75, r, a = sma(c, 25), sma(c, 75), rsi(c), atr(bars)
    e12, e26 = ema(c, 12), ema(c, 26)
    macd = [x - e26[k] for k, x in enumerate(e12)]
    sig = ema(macd, 9)
    hist = [x - sig[k] for k, x in enumerate(macd)]
    vol20 = sma(v, 20)
    A = dict(c=c[i], prev=c[i - 1], date=bars[i]["d"], sma25=s25[i], sma75=s75[i], sma75p=s75[i - 10], rsi=r[i],
             hist=hist[i], histP=hist[i - 1], atr=a[i], volRatio=(v[i] / vol20[i]) if vol20[i] else 1)
    A["chg"] = A["c"] / A["prev"] - 1
    A["dev25"] = A["c"] / A["sma25"] - 1
    A["atrPct"] = A["atr"] / A["c"]
    A["max20"] = max(c[-20:])
    trend = A["c"] > A["sma75"] and A["sma25"] > A["sma75"] and A["sma75"] > A["sma75p"]
    cks = [
        (25, trend, "上昇トレンド"),
        (15, -0.05 <= A["dev25"] <= 0.02, "25日線付近の押し目"),
        (15, 35 <= A["rsi"] <= 60, "RSI"),
        (15, A["hist"] > A["histP"], "MACD改善"),
        (10, A["atrPct"] <= 0.035, "値動きが穏やか"),
        (10, A["volRatio"] >= 0.7, "出来高"),
        (10, A["chg"] <= 0.04, "急騰直後ではない"),
    ]
    A["score"] = sum(w for w, ok, _ in cks if ok)
    A["allOk"] = all(ok for _, ok, _ in cks)
    A["missing"] = [t for _, ok, t in cks if not ok]
    return A


# ---------- advice ----------
def floor100(x):
    return int(math.floor(x / 100) * 100)


def compute(cfg, data):
    st = {**DEFAULT_SET, **cfg.get("settings", {})}
    names = {w["code"]: w["name"] for w in cfg["watch"]}
    allA = {w["code"]: analyze(data.get(w["code"])) for w in cfg["watch"]}
    for h in cfg.get("holdings", []):
        allA.setdefault(h["code"], analyze(data.get(h["code"])))
    ok_list = [a for a in (allA[w["code"]] for w in cfg["watch"]) if a]
    n = len(ok_list)
    breadth = (sum(1 for a in ok_list if a["c"] > a["sma75"]) / n) if n else None
    regime, max_new = "unknown", st["maxNew"]
    if breadth is not None:
        regime = "warn" if n < 3 else "ok" if breadth >= 0.5 else "warn" if breadth >= 0.3 else "stop"
    if regime == "warn":
        max_new = min(max_new, 1)
    if regime == "stop":
        max_new = 0

    held, held_value, sells, holds = {}, 0.0, [], []
    for h in cfg.get("holdings", []):
        held[h["code"]] = 1
        a = allA.get(h["code"])
        if not a:
            continue
        held_value += a["c"] * h["shares"]
        stop = h["stop"] if h.get("stop", 0) > 0 else h["avg"] - st["stopAtr"] * a["atr"]
        if a["c"] >= h["avg"] + 1.5 * a["atr"]:
            stop = max(stop, h["avg"])
        if a["max20"] >= h["avg"] + 2.5 * a["atr"]:
            stop = max(stop, a["max20"] - 2.5 * a["atr"])
        kind, why, qty = "hold", None, h["shares"]
        if a["c"] <= stop:
            kind, why = "sell", f"終値が損切り/トレーリング価格 {stop:,.0f}円 に到達"
        elif a["c"] < a["sma75"]:
            kind, why = "sell", f"終値が75日線({a['sma75']:,.0f}円)を割り込み、上昇トレンドが終了"
        elif a["c"] < a["sma25"] and a["hist"] < 0 and a["hist"] < a["histP"]:
            kind, why = "sell", "25日線割れ + MACDヒストグラムが負で悪化"
        elif a["rsi"] >= 75 and h["shares"] >= 200:
            kind, qty, why = "trim", floor100(h["shares"] / 2), f"RSI {a['rsi']:.0f} と過熱。半分を利益確定"
        item = dict(code=h["code"], name=names.get(h["code"], ""), kind=kind, qty=qty, stop=round(stop), avg=h["avg"],
                    pnl=a["c"] / h["avg"] - 1, why=why)
        (holds if kind == "hold" else sells).append(item)

    cand_items = [(w["code"], allA[w["code"]]) for w in cfg["watch"]
                  if allA[w["code"]] and w["code"] not in held and allA[w["code"]]["allOk"]]
    cand_items.sort(key=lambda x: (-x[1]["score"], x[1]["atrPct"]))
    watch = [dict(code=w["code"], name=w["name"], score=a["score"], missing=a["missing"]) for w in cfg["watch"]
             if (a := allA[w["code"]]) and w["code"] not in held and not a["allOk"] and a["score"] >= 75]
    held_count = len(cfg.get("holdings", [])) - sum(1 for s in sells if s["kind"] == "sell")
    slots = max(0, min(max_new, st["maxPositions"] - held_count))
    budget = st["capital"] * (1 - st["cashPct"] / 100) - held_value
    buys, skipped = [], []
    for code, a in cand_items:
        if len(buys) >= slots:
            skipped.append(dict(code=code, name=names[code], why="新規の上限または保有上限に達している"))
            continue
        entry = a["c"]
        stop = entry - st["stopAtr"] * a["atr"]
        rps = entry - stop
        q1 = floor100(st["capital"] * st["riskPct"] / 100 / rps)
        q2 = floor100(st["capital"] * st["maxPosPct"] / 100 / entry)
        q3 = floor100(max(0, budget) / entry)
        qty = min(q1, q2, q3)
        if qty < 100:
            skipped.append(dict(code=code, name=names[code], why=f"資金・リスク枠の範囲では100株を買えない(必要資金 {entry * 100:,.0f}円)"))
            continue
        budget -= qty * entry
        buys.append(dict(code=code, name=names[code], qty=qty, entry=round(entry), stop=round(stop),
                         target=round(entry + 1.5 * rps), cost=round(qty * entry), risk=round(qty * rps), score=a["score"],
                         rsi=round(a["rsi"]), atrPct=round(a["atrPct"] * 100, 1)))
    asof = max((a["date"] for a in allA.values() if a), default="")
    return dict(asof=asof, regime=regime, breadth=breadth, withData=n, maxNew=max_new, buys=buys, sells=sells, holds=holds,
                watch=watch, skipped=skipped)


# ---------- message ----------
REGIME = {"ok": "通常", "warn": "慎重(新規は最大1銘柄)", "stop": "新規買い停止", "unknown": "判定不可"}


def message(adv, site_url=""):
    L = [f"【朝9時の売買アドバイス】基準: {adv['asof']} の終値",
         f"地合い: {REGIME[adv['regime']]}" + (f"(75日線の上 {adv['breadth'] * 100:.0f}% / {adv['withData']}銘柄)" if adv["breadth"] is not None else "")]
    if adv["sells"]:
        L.append("■ 売り")
        for s in adv["sells"]:
            L.append(f"  {s['code']} {s['name']}: {'全株売却' if s['kind'] == 'sell' else '半分利確'} {s['qty']:,}株 寄付き成行 "
                     f"(損益 {s['pnl'] * 100:+.1f}%) {s['why']}")
    if adv["buys"]:
        L.append("■ 新規買い(始値が前日終値+2%超なら見送り。約定後すぐ逆指値)")
        for b in adv["buys"]:
            L.append(f"  {b['code']} {b['name']}: {b['qty']:,}株 指値{b['entry']:,}円 / 損切り{b['stop']:,}円 / 利確目安{b['target']:,}円 "
                     f"/ 投入{b['cost']:,}円 / 最大損失{b['risk']:,}円")
    else:
        L.append("■ 新規買い: 推奨なし(条件を満たす銘柄なし、または地合い・枠の制限)")
    if adv["holds"]:
        L.append("■ 保有継続(逆指値の位置)")
        for h in adv["holds"]:
            L.append(f"  {h['code']} {h['name']}: 逆指値 {h['stop']:,}円 (損益 {h['pnl'] * 100:+.1f}%)")
    if adv["watch"]:
        L.append("■ 監視中: " + "、".join(f"{w['code']} {w['name']}" for w in adv["watch"]))
    if site_url:
        L.append(f"詳細: {site_url}")
    L.append("※参考情報であり投資助言ではありません。発注はご自身で行ってください。")
    return "\n".join(L)


def notify(text):
    url = os.environ.get("NOTIFY_WEBHOOK_URL", "").strip()
    if not url:
        print("NOTIFY_WEBHOOK_URL 未設定のため通知はスキップ")
        return
    key = "content" if "discord.com" in url else "text"
    body = json.dumps({key: text[:1900]}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()
    print("通知を送信しました")


def is_market_day(now):
    if now.weekday() >= 5:
        return False
    try:
        import jpholiday
        return not jpholiday.is_holiday(now.date()) and not (now.month == 12 and now.day == 31) and not (now.month == 1 and now.day <= 3)
    except ImportError:
        return True


def main():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    dpath = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "data.json"
    data = json.loads(dpath.read_text(encoding="utf-8"))["data"]
    adv = compute(cfg, data)
    now = datetime.now(JST)
    adv["generated"] = now.strftime("%Y-%m-%d %H:%M JST")
    (ROOT / "docs" / "advice.json").write_text(json.dumps(adv, ensure_ascii=False, indent=1), encoding="utf-8")
    text = message(adv, os.environ.get("SITE_URL", ""))
    print(text)
    if os.environ.get("FORCE_NOTIFY") == "1" or is_market_day(now):
        notify(text)
    else:
        print("本日は休場日のため通知しません")


if __name__ == "__main__":
    main()

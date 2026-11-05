#!/usr/bin/env python3
"""config.json の監視銘柄の日足を取得して docs/data.json に書き出す。
取得元: yfinance(Yahoo!ファイナンス) を優先し、失敗したら Stooq にフォールバック。"""
import csv, io, json, sys, time, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))
MIN_BARS = 80


def fetch_yfinance(code):
    import yfinance as yf
    df = yf.Ticker(f"{code}.T").history(period="2y", interval="1d", auto_adjust=False)
    rows = []
    for idx, r in df.iterrows():
        vals = [r["Open"], r["High"], r["Low"], r["Close"]]
        if any(v != v or v <= 0 for v in vals):  # NaN / 0 を除外
            continue
        rows.append([idx.strftime("%Y-%m-%d"), *[round(float(v), 2) for v in vals], int(r["Volume"] or 0)])
    return rows


def fetch_stooq(code):
    url = f"https://stooq.com/q/d/l/?s={code.lower()}.jp&i=d"
    req = urllib.request.Request(url, headers={"User-Agent": "kabu-advisor/1.0"})
    text = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    if not text.lower().startswith("date"):
        raise RuntimeError("Stooq がCSVを返しませんでした")
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        try:
            o, h, l, c = (float(r[k]) for k in ("Open", "High", "Low", "Close"))
            rows.append([r["Date"], o, h, l, c, int(float(r.get("Volume") or 0))])
        except (ValueError, KeyError):
            continue
    return rows


def get_rows(code):
    errors = []
    for name, fn in (("yfinance", fetch_yfinance), ("stooq", fetch_stooq)):
        for attempt in range(2):
            try:
                rows = fn(code)
                if len(rows) >= MIN_BARS:
                    return name, rows[-400:]
                errors.append(f"{name}: {len(rows)}本しか取得できず")
                break
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {e}")
                time.sleep(2)
    raise RuntimeError(" / ".join(errors))


def main():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    codes = [w["code"] for w in cfg["watch"]] + [h["code"] for h in cfg.get("holdings", [])]
    codes = list(dict.fromkeys(codes))
    data, sources, failed = {}, set(), []
    for code in codes:
        try:
            src, rows = get_rows(code)
            data[code] = rows
            sources.add(src)
            print(f"OK   {code}: {len(rows)}本 最終日 {rows[-1][0]} ({src})")
        except Exception as e:  # noqa: BLE001
            failed.append(code)
            print(f"FAIL {code}: {e}", file=sys.stderr)
    if len(data) < max(1, len(codes) // 2):
        sys.exit("取得できた銘柄が半数未満のため中止します(前回のページを残します)")
    out = {
        "generated": datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
        "source": "/".join(sorted(sources)),
        "failed": failed,
        "settings": cfg.get("settings", {}),
        "watch": cfg["watch"],
        "holdings": cfg.get("holdings", []),
        "data": data,
    }
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "data.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"docs/data.json を書き出しました({len(data)}銘柄、失敗 {len(failed)})")


if __name__ == "__main__":
    main()

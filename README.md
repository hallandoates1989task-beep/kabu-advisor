# 朝9時の売買アドバイザー(自動実行版)

平日の朝8時に GitHub Actions が動き、次の順で処理します。

1. `scripts/fetch_prices.py` が監視銘柄の日足を取得(yfinance、失敗時は Stooq)して `docs/data.json` を作る
2. `scripts/advisor.py` がアドバイスを計算し、Slack / Discord に通知(`docs/advice.json` にも保存)
3. `docs/` を GitHub Pages に公開。ページは開くたびに最新の `data.json` を読み込んで表示

売買ロジックはページ(`docs/index.html`)と `advisor.py` で同一です。注文は自動では出ません。

## セットアップ(10分)

1. GitHub で新しいリポジトリを作り、このフォルダの中身をすべて push する(ブランチ名は `main`)
2. **Settings → Pages → Source** を **GitHub Actions** にする
3. 通知を使う場合は **Settings → Secrets and variables → Actions** に `NOTIFY_WEBHOOK_URL` を登録する
   (Slack の Incoming Webhook か Discord のウェブフック URL)。未登録なら通知なしで、ページだけ更新されます
4. **Actions → 朝のアドバイス更新 → Run workflow** で一度手動実行し、成功すること、
   `https://<ユーザー名>.github.io/<リポジトリ名>/` にページが出ることを確認する

## 日々の使い方

- 保有銘柄・資金・リスク設定・監視銘柄は **`config.json`** を編集して commit する(push すると自動で再実行)。
  `holdings` の例: `{"code": "7203", "shares": 200, "avg": 2754, "stop": 0}`(`stop` は 0 で自動計算)
- 約定したら `holdings` に追加、売ったら削除する。これが売り判断の元になります

## 注意

- 祝日・年末年始は `jpholiday` で判定して通知を止めます(ページは前営業日の内容のまま)
- GitHub Actions の定時実行は数分〜数十分遅れることがあります。8:00 起動にしているのはそのためです
- yfinance は非公式の取得手段で、仕様変更で止まることがあります。その場合は Stooq に切り替わり、
  どちらも失敗するとジョブが失敗して GitHub から通知が来ます(前回のページはそのまま残ります)
- 本ツールは過去の価格から機械的に計算した参考情報で、投資助言ではありません。

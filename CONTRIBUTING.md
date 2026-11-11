# Contributing

## ルール

1. `main` へ直接コミットしない
2. 基本は `develop` から作業ブランチを作る
3. 1 PR = 1テーマ
4. 機能追加と大規模リファクタを混ぜない
5. 個人画像・非公開作品・認証情報をコミットしない
6. 最低限 `python -m py_compile app.py` を通す
7. GUI変更は実機確認内容をPRに書く

## ブランチ例

- `feature/gif-support`
- `feature/effect-template`
- `fix/corner-resize`
- `fix/undo-history`

## コミット例

- `feat: add gif effect prototype`
- `fix: preserve panel geometry on resize`
- `docs: update collaboration workflow`
- `test: add static source checks`

## PRに書くもの

- 目的
- 変更内容
- 影響範囲
- テスト内容
- 未確認事項
- UI変更時の公開可能なスクリーンショット

現在 `app.py` が大きいため、同じ箇所の同時編集は避けてください。
作業開始前にIssueで担当範囲を宣言する運用を推奨します。
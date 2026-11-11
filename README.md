# Manga Peta Editor

Windows向けの軽量な漫画レイアウト／画像編集エディタです。  
現在の基準版は **v1.8.4-debug** です。

## 主な機能

- 複数ページ編集
- 画像の配置・回転・倍率・不透明度
- コマ生成 / N分割 / 自由四隅編集
- `Tab` で選択コマの四隅編集 ON/OFF
- コマ画像のパン・ズーム
- 横書き / 縦書き文字
- 吹き出し
- Effectフォルダ素材
- Effect素材の移動・拡縮・回転
- 通常Undo/Redo + Effect専用Undo/Redo
- PNG / 縦長PNG / ZIP出力
- プロジェクト保存 / 日付保存 / 復旧保存
- 更新用 `.py` のD&D適用
- レイヤー詳細データ表示

## 起動

Windowsでは `run.bat` を実行してください。

手動の場合:

```bash
python -m pip install -r requirements.txt
python app.py
```

## 共同開発

通常は `develop` から作業ブランチを作り、Pull Requestを `develop` に出します。

- `main` — 動作確認済み基準
- `develop` — 次版統合
- `feature/*` — 新機能
- `fix/*` — バグ修正
- `docs/*` — 文書

詳細: [docs/TEAM_WORKFLOW.md](docs/TEAM_WORKFLOW.md)

## 注意

`Projects/`、個人作品、実画像、ローカルEffect素材、`*_assets/` はGit管理対象外です。
公開Issue/PRへ個人情報や非公開素材を貼らないでください。

## License

現段階では正式ライセンスを未決定にしています。
勝手にMIT等へ変更せず、共同開発開始後に方針を決める想定です。
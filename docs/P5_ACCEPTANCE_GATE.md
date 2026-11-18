# P5 — Blender風コマ編集 最終受入ゲート

基準: Issue #5 / Team A。対象は develop の P0〜P4 実装および Gキー吸着 PR #12。
この文書は**実機合格の宣言ではない**。自動テストと人間によるWindows GUI試験を分離する。

## 判定規則

- **A 自動ゲート**: 最新developとPR両方のWindows CIが成功。全pytest/unittestではなく、CI定義の \`unittest discover\`、構文検査、プロジェクト検査を通す。
- **B Windows実機ゲート**: 操作担当者が下表を実際に操作し、結果・OS・Python版・画面解像度をIssue #5に残す。
- **C 公開/安定版ゲート**: A/B合格後、既存の動作を後退させないことを確認し develop→main。GUI未確認の間はmainを変更しない。
- 判定: Aのみなら「実装・CI合格／実機未受入」。A+B+Cを満たせば「Blender風コマ編集 P5受入完了」。GIF等の将来候補はP5範囲外。

## A — 自動テスト（人工素材のみ）

CI: \`python -m py_compile app.py\`、\`python scripts/check_project.py\`、\`QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -p "test_*.py"\`

| 検証軸 | 根拠 | 結果 |
|---|---|---|
| Shift+A/Shift+D/G/X、数値/軸入力、誤入力防止 | test_panel_shortcuts / test_panel_grab | CI結果待ち |
| G吸着、キャンセル、数値は正確、複数コマ | test_panel_grab_snapping | CI結果待ち |
| 四角Ctrl+R分割、プレビューとキャンセル | test_panel_split_preview | CI結果待ち |
| N角形の頂点追加・変形・保存 | test_panel_polygon | CI結果待ち |
| 2頂点分割、凹多角形、ギャップ、履歴 | test_panel_chord_split | CI結果待ち |
| 実MangaEditorのページ切替・Undo/Redo、画像とEffect継承、保存/旧形式 | test_p5_integrated_qa | CI結果待ち |
| Effect Undo/Redo、ページ分離、復旧チェックポイント | test_p5_integrated_qa | CI結果待ち |
| PNG/縦長PNG/ZIP書き出しと画像のデコード | test_p5_integrated_qa | CI結果待ち |
| .py更新ファイルの静的検査、最小画面での描画 | test_p5_integrated_qa | CI結果待ち |

**注意**: オフスクリーン出力のデコード成功は、画像と重ね合わせの外観が期待どおりである保証ではない。

## B — Windows実機の検証手順（未実施）

試験環境: Windows __、Python __、PySide6 __、画面 __×__、アプリの取得元HEAD __、実施者 __、日付 __。
すべて人工素材・別名のテストプロジェクトで行い、既存の作品/Effectフォルダを上書きしない。

1. **G操作と優先順位**: コマを選択→G→マウスで他コマへ吸着。複数コマで位置関係が維持されるか、選択頂点/辺での吸着、X/Y固定、数値入力、Esc取消、Ctrl+Z/Yを確認。文字入力中のXやGではコマが消えないこと。
2. **四角分割**: Ctrl+Rで橙色の破線、マウス方向変更、ホイール2〜12、隙間24px、Esc取消、確定後の1回Undoを確認。ありえない隙間では赤プレビューで拒否。
3. **N角形と2頂点切断**: Tab編集→Shift+辺→Eで5/6角形→Gで凹形状→Ctrl+非隣接2頂点→Ctrl+R。橙破線と隙間の2本を確認。凹形状・無効弦・隙間0/極大・回転/拡縮も試し、無効時は元コマを残す。
4. **画像/Effectと通常履歴**: 画像付きコマで分割、画像の位置・クリッピング・透明度・重ね素材を目視。分割→Undo/Redo→Effect専用Undo/Redo→ページ切替→戻る、で画像と履歴が破綻しないこと。
5. **保存/復元**: 旧4頂点プロジェクトとN角形プロジェクトを読み込み、保存→閉じる→再起動→読込を確認。保存後の素材内包を確認。強制終了は避け、別プロファイルでの未保存復旧を実施する。
6. **出力**: 1ページPNG、縦長PNG、全ページZIPを出し、実際に表示・目視する。白紙化、ページ順、画像/Effect位置、文字、透明度、コマ外の切り抜けがないか。
7. **更新.py（隔離環境のみ）**: テスト専用のコピーに旧app.pyを置き、更新.pyをD&D。バックアップが作られるか、再起動するか、保存前の作業を復旧できるか確認。**実作業に利用中のインストールに直接更新テストをしない**。
8. **小画面**: 1366×768、可能なら900×560でも、スクロール・下部の操作・ページ一覧・Effect・分割設定が操作できるか。表示倍率変更と戻る操作も確認する。

記録欄（各項目）：PASS / FAIL / BLOCKED / 未実施、手順と実際の結果、スクリーンショット（公開可能な人工素材のみ）、Issue/PRリンク。

## 現段階のストップ条件

- CIに失敗があればP5は未達。修正し再テストする。
- 実機GUI未実施、更新D&D未実施なら **P5最終受入を宣言しない**。
- 同じP5課題として扱い、GIF/アニメ/その他の新企画を追加しない。

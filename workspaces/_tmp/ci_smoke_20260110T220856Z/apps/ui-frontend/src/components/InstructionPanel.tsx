export function InstructionPanel() {
  return (
    <div className="instruction-panel">
      <h2>使い方ガイド</h2>
      <p className="muted">
        このダッシュボードは、台本・音声制作フェーズの進捗とファイルをまとめて管理するための画面です。
        スプレッドシートと同期した <strong>status.json</strong> を唯一の真実とし、作業結果をここから編集・保存できます。
      </p>
      <ol>
        <li><strong>左カラム</strong>でチャンネル → 動画を順に選択します。</li>
        <li>進捗テーブルで現在のステージを確認し、必要に応じてステータスを更新します。</li>
        <li>台本本文・音声用テキスト・字幕を編集したら、それぞれの「保存」ボタンを押します。</li>
        <li>音声ファイルが生成済みの場合は、画面下部で試聴できます。</li>
      </ol>
      <div className="instruction-highlight">
        <h3>制作フロー（AI 95% 自動化）</h3>
        <p>
          ① 企画採用 → ② 台本自動生成 → ③ 音声読み上げ・字幕 → ④ 画像生成 → ⑤ 動画編集 → ⑥ サムネ調整 → ⑦ 投稿・分析
        </p>
        <p>
          本UIでは特に <strong>② 台本</strong> と <strong>③ 音声</strong> に関する成果物を確認・修正できます。
        </p>
      </div>
      <p className="muted small">
        詳細なオペレーション手順は <code>ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md</code> と <code>ssot/ops/OPS_SCRIPT_GUIDE.md</code> を参照してください。
      </p>
    </div>
  );
}

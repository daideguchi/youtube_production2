export interface MockPageDefinition {
  id: string;
  label: string;
  description?: string;
  assetPath: string;
}

export const mockPages: MockPageDefinition[] = [
  {
    id: "dashboard",
    label: "モック1: ダッシュボード",
    description: "主要KPIを俯瞰するハブ画面",
    assetPath: "/mock/dashboard-main.html",
  },
  {
    id: "project-list",
    label: "モック2: 企画一覧",
    description: "チャンネル別の企画一覧ビュー",
    assetPath: "/mock/project-list.html",
  },
  {
    id: "case-overview",
    label: "モック3: 案件概要タブ",
    description: "案件メタ情報とアクティビティ要約",
    assetPath: "/mock/case-overview.html",
  },
  {
    id: "script-audio-editor",
    label: "モック4: 台本・音声編集",
    description: "台本・音声・字幕の編集ワークフロー",
    assetPath: "/mock/script-audio-editor.html",
  },
  {
    id: "detail-metadata",
    label: "モック5: 詳細メタ情報",
    description: "生成履歴・同期履歴・エラーの確認画面",
    assetPath: "/mock/detail-metadata.html",
  },
];

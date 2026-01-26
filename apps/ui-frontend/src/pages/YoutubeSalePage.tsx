import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useOutletContext } from "react-router-dom";
import type { ChannelSummary } from "../api/types";
import type { ShellOutletContext } from "../layouts/AppShell";

function copyToClipboard(text: string): Promise<boolean> {
  const value = String(text ?? "");
  if (!value) {
    return Promise.resolve(false);
  }
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    return navigator.clipboard
      .writeText(value)
      .then(() => true)
      .catch(() => false);
  }
  try {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(textarea);
    return Promise.resolve(Boolean(ok));
  } catch {
    return Promise.resolve(false);
  }
}

export type ListingMode = "normal" | "closed";
export type OwnershipKind = "unknown" | "brand" | "personal";
export type OperatorDependency = "unknown" | "low" | "medium" | "high";

export type RaccoFees = {
  rate: number;
  minFeeYen: number;
  listingMode: ListingMode;
  salePriceYen: number;
  buyerFeeYen: number;
  sellerFeeYen: number;
  buyerPaysYen: number;
  sellerReceivesYen: number;
};

export function calcRaccoFeeYen(
  salePriceYen: number,
  options?: {
    rate?: number;
    minFeeYen?: number;
  }
): number {
  const rate = options?.rate ?? 0.05;
  const minFeeYen = options?.minFeeYen ?? 55_000;
  const price = Number.isFinite(salePriceYen) ? Math.max(0, Math.floor(salePriceYen)) : 0;
  const fee = Math.round(price * rate);
  return Math.max(fee, minFeeYen);
}

export function calcRaccoFees(salePriceYen: number, listingMode: ListingMode): RaccoFees {
  const rate = 0.05;
  const minFeeYen = 55_000;
  const salePrice = Number.isFinite(salePriceYen) ? Math.max(0, Math.floor(salePriceYen)) : 0;
  const buyerFeeYen = calcRaccoFeeYen(salePrice, { rate, minFeeYen });
  const sellerFeeYen = listingMode === "closed" ? calcRaccoFeeYen(salePrice, { rate, minFeeYen }) : 0;
  return {
    rate,
    minFeeYen,
    listingMode,
    salePriceYen: salePrice,
    buyerFeeYen,
    sellerFeeYen,
    buyerPaysYen: salePrice + buyerFeeYen,
    sellerReceivesYen: Math.max(0, salePrice - sellerFeeYen),
  };
}

export type YoutubeSaleDraft = {
  listingMode: ListingMode;
  ownership: OwnershipKind;
  operatorDependency: OperatorDependency;
  salePriceYen: string;
  avgMonths: string;
  subscribers: string;
  monthlyViews: string;
  monthlyProfitYen: string;
  rightsNote: string;
  customNote: string;
  includePrompts: boolean;
  includeManual: boolean;
};

type Template = {
  key: string;
  title: string;
  desc: string;
  body: string;
};

function safeGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* no-op */
  }
}

function safeRemove(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    /* no-op */
  }
}

function parsePositiveInt(raw: string): number | null {
  const cleaned = String(raw ?? "")
    .replace(/[,_\s]/g, "")
    .replace(/[^\d]/g, "");
  if (!cleaned) {
    return null;
  }
  const n = Number.parseInt(cleaned, 10);
  if (!Number.isFinite(n) || n <= 0) {
    return null;
  }
  return n;
}

function formatYen(value: number): string {
  const n = Number.isFinite(value) ? Math.floor(value) : 0;
  return new Intl.NumberFormat("ja-JP").format(n);
}

function formatCount(value: number): string {
  const n = Number.isFinite(value) ? Math.floor(value) : 0;
  return new Intl.NumberFormat("ja-JP").format(n);
}

function compareChannelCode(a: string, b: string): number {
  const an = Number.parseInt(a.replace(/[^0-9]/g, ""), 10);
  const bn = Number.parseInt(b.replace(/[^0-9]/g, ""), 10);
  const aNum = Number.isFinite(an);
  const bNum = Number.isFinite(bn);
  if (aNum && bNum) {
    return an - bn;
  }
  if (aNum) return -1;
  if (bNum) return 1;
  return a.localeCompare(b, "ja-JP");
}

function resolveChannelDisplayName(channel: ChannelSummary): string {
  return channel.name ?? channel.branding?.title ?? channel.youtube_title ?? channel.code;
}

export type SaleDossierParams = {
  channel: ChannelSummary | null;
  channelCode: string | null;
  draft: YoutubeSaleDraft;
};

export function generateRaccoListingText({ channel, channelCode, draft }: SaleDossierParams): string {
  const salePriceYen = parsePositiveInt(draft.salePriceYen);
  const priceLabel = salePriceYen ? `${formatYen(salePriceYen)}円（税込）` : "{売却価格}円（税込）";
  const fees = salePriceYen ? calcRaccoFees(salePriceYen, draft.listingMode) : null;
  const buyerTotalLabel = fees ? `${formatYen(fees.buyerPaysYen)}円` : "{買主支払総額}円";

  const channelLabel = channel ? resolveChannelDisplayName(channel) : channelCode ? channelCode : "{CH}";
  const handle = channel?.youtube_handle ? `@${channel.youtube_handle}` : "";
  const genre = (channel?.genre || "").trim();
  const branding = channel?.branding ?? null;
  const youtubeUrl = (branding?.url || "").trim();
  const launchDate = (branding?.launch_date || "").trim();

  const avgMonths = parsePositiveInt(draft.avgMonths);
  const monthlyProfitYen = parsePositiveInt(draft.monthlyProfitYen);
  const subscribersManual = parsePositiveInt(draft.subscribers);
  const subscriberCount = subscribersManual ?? branding?.subscriber_count ?? null;
  const monthlyViews = parsePositiveInt(draft.monthlyViews);
  const totalViews = branding?.view_count ?? null;
  const totalVideos = branding?.video_count ?? channel?.video_count ?? null;

  const dependencyLabel =
    draft.operatorDependency === "low"
      ? "低い"
      : draft.operatorDependency === "medium"
        ? "中"
        : draft.operatorDependency === "high"
          ? "高い"
          : "不明";

  const ownershipLabel =
    draft.ownership === "brand" ? "ブランドアカウント" : draft.ownership === "personal" ? "個人アカウント" : "不明";

  const deliverables: string[] = [];
  deliverables.push("動画一式（既存投稿分）");
  if (draft.includePrompts) deliverables.push("台本テンプレ / サムネテンプレ（プロンプト）");
  if (draft.includeManual) deliverables.push("運用マニュアル（最小）");
  deliverables.push("※自動化システム/内部ツール/リポジトリは提供しません");

  const lines: string[] = [];
  lines.push("【案件概要】");
  lines.push(`- 売却対象: YouTubeチャンネル「${channelLabel}」${handle ? `（${handle}）` : ""}`);
  if (youtubeUrl) lines.push(`- URL: ${youtubeUrl}`);
  if (genre) lines.push(`- ジャンル: ${genre}`);
  lines.push(`- 売却価格: ${priceLabel}`);
  lines.push(`- 想定（買主支払総額）: ${buyerTotalLabel}（手数料はラッコM&A規定）`);
  if (monthlyProfitYen) {
    lines.push(`- 収益: 月 ${formatYen(monthlyProfitYen)}円（利益の目安）${avgMonths ? ` / 直近${avgMonths}ヶ月` : ""}`);
  }
  if (subscriberCount) lines.push(`- 登録者: 約 ${formatCount(subscriberCount)} 人`);
  if (totalVideos) lines.push(`- 動画数: 約 ${formatCount(totalVideos)} 本`);
  if (monthlyViews) lines.push(`- 月間再生: 約 ${formatCount(monthlyViews)} 回（目安）`);
  else if (totalViews) lines.push(`- 総再生: 約 ${formatCount(totalViews)} 回`);
  if (launchDate) lines.push(`- 開設: ${launchDate}`);
  lines.push(`- 属人性: ${dependencyLabel}`);
  lines.push(`- アカウント形態: ${ownershipLabel}`);
  lines.push("");
  lines.push("【譲渡方法（重要）】");
  lines.push("- 基本は「ブランドアカウント」の権限移行で対応します。");
  lines.push("- オーナー招待 → 7日経過 → メインのオーナー移行 → 売主削除、の順が基本です。");
  lines.push("- AdSenseは譲渡不可のため、買主側のAdSenseへ差し替えになります。");
  lines.push("");
  lines.push("【提供物（最小パック）】");
  for (const item of deliverables) {
    lines.push(`- ${item}`);
  }
  if (draft.rightsNote.trim()) {
    lines.push("");
    lines.push("【権利/注意事項】");
    lines.push(draft.rightsNote.trim());
  }
  if (draft.customNote.trim()) {
    lines.push("");
    lines.push("【補足】");
    lines.push(draft.customNote.trim());
  }

  return lines.join("\n");
}

export function generateBuyerMessageText({ channel, channelCode, draft }: SaleDossierParams): string {
  const salePriceYen = parsePositiveInt(draft.salePriceYen);
  const priceLabel = salePriceYen ? `${formatYen(salePriceYen)}円（税込）` : "{売却価格}円（税込）";
  const channelLabel = channel ? resolveChannelDisplayName(channel) : channelCode ? channelCode : "{CH}";

  const lines: string[] = [];
  lines.push(`件名: YouTubeチャンネル譲渡の進め方（${channelLabel}）`);
  lines.push("");
  lines.push("こんにちは。お取引ありがとうございます。譲渡は以下の流れで進めます。");
  lines.push("");
  lines.push("【1) 事前にご用意いただきたいもの】");
  lines.push("- 受け取り用のGoogleアカウント（メール）");
  lines.push("- 収益化する場合は、買主様ご自身のAdSense（譲渡は不可のため差し替え）");
  lines.push("");
  lines.push("【2) 権限移行（基本）】");
  lines.push("- 私（売主）から、買主様を“オーナー”として招待します");
  lines.push("- 招待後、YouTube側仕様により「7日」経過後に買主様を“メインのオーナー”へ変更します");
  lines.push("- その後、売主側の権限を削除します");
  lines.push("");
  lines.push("【3) 検収（例）】");
  lines.push("- 買主様がメインのオーナーになっていること");
  lines.push("- YouTube Studio / Analyticsに問題なくアクセスできること");
  lines.push("- 提供物（テンプレ/マニュアル等）が受領できていること");
  lines.push("");
  lines.push(`【4) 価格】\n- ${priceLabel}`);
  lines.push("");
  lines.push("不明点があれば、このスレッドでいつでもご連絡ください。");
  return lines.join("\n");
}

export function generateAcceptanceCriteriaText({ channel, channelCode, draft }: SaleDossierParams): string {
  const channelLabel = channel ? resolveChannelDisplayName(channel) : channelCode ? channelCode : "{CH}";
  const deliverables: string[] = [];
  if (draft.includePrompts) deliverables.push("台本/サムネのテンプレ（プロンプト）");
  if (draft.includeManual) deliverables.push("運用マニュアル（最小）");
  if (deliverables.length === 0) deliverables.push("（提供物の選択なし）");

  const lines: string[] = [];
  lines.push(`【検収条件（テンプレ）: ${channelLabel}】`);
  lines.push("- YouTubeチャンネルのメインのオーナーが買主に移行している");
  lines.push("- 売主の不要な権限が削除され、買主が単独で管理できる");
  lines.push("- AdSenseは買主側で差し替え（譲渡不可）");
  lines.push("");
  lines.push("【提供物の検収】");
  for (const item of deliverables) {
    lines.push(`- ${item} が共有されている`);
  }
  lines.push("- 提供物に自動化システム/内部ツール/リポジトリは含まれない（前提の確認）");
  lines.push("");
  lines.push("【検収期間（例）】");
  lines.push("- メインオーナー移行完了日から 3〜7日（双方合意の上で確定）");
  return lines.join("\n");
}

export function generateDeliveryChecklistText({ channel, channelCode, draft }: SaleDossierParams): string {
  const channelLabel = channel ? resolveChannelDisplayName(channel) : channelCode ? channelCode : "{CH}";
  const lines: string[] = [];
  lines.push(`【納品チェックリスト: ${channelLabel}】`);
  lines.push("- [ ] 買主のGoogleアカウント（招待先）を受領");
  lines.push("- [ ] 買主をオーナーとして招待");
  lines.push("- [ ] 7日経過後、買主をメインのオーナーへ変更");
  lines.push("- [ ] 売主権限を削除（必要なら管理者も整理）");
  lines.push("- [ ] AdSense差し替え（買主側）");
  lines.push("- [ ] YouTube Studio/Analyticsログイン確認（買主側）");
  if (draft.includePrompts) lines.push("- [ ] 台本/サムネのテンプレ（プロンプト）を共有");
  if (draft.includeManual) lines.push("- [ ] 運用マニュアル（最小）を共有");
  lines.push("- [ ] （任意）素材/権利の注意点を共有");
  lines.push("- [ ] 検収OK → エスクロー決済/完了");
  return lines.join("\n");
}

function defaultDraft(): YoutubeSaleDraft {
  return {
    listingMode: "normal",
    ownership: "unknown",
    operatorDependency: "unknown",
    salePriceYen: "690000",
    avgMonths: "6",
    subscribers: "",
    monthlyViews: "",
    monthlyProfitYen: "",
    rightsNote: "",
    customNote: "",
    includePrompts: true,
    includeManual: true,
  };
}

export function YoutubeSalePage() {
  const { channels, channelsLoading, channelsError, selectedChannel, selectChannel } =
    useOutletContext<ShellOutletContext>();
  const navigate = useNavigate();
  const location = useLocation();

  const [copyStatus, setCopyStatus] = useState<string | null>(null);
  const [draft, setDraft] = useState<YoutubeSaleDraft>(() => defaultDraft());

  const channelFromQuery = useMemo(() => {
    const params = new URLSearchParams(location.search);
    const code = (params.get("channel") || "").trim().toUpperCase();
    return code || null;
  }, [location.search]);

  useEffect(() => {
    if (!channelFromQuery) {
      return;
    }
    if (channelFromQuery !== selectedChannel) {
      selectChannel(channelFromQuery);
    }
  }, [channelFromQuery, selectChannel, selectedChannel]);

  const storageKey = useMemo(() => {
    const code = selectedChannel ?? channelFromQuery ?? null;
    const suffix = code ? code : "__global__";
    return `ui.youtubeSale.draft.v1.${suffix}`;
  }, [channelFromQuery, selectedChannel]);

  useEffect(() => {
    const raw = safeGet(storageKey);
    if (!raw) {
      setDraft(defaultDraft());
      return;
    }
    try {
      const parsed = JSON.parse(raw) as Partial<YoutubeSaleDraft>;
      setDraft({
        ...defaultDraft(),
        ...parsed,
        listingMode: parsed.listingMode === "closed" ? "closed" : "normal",
        ownership:
          parsed.ownership === "brand" ? "brand" : parsed.ownership === "personal" ? "personal" : ("unknown" as OwnershipKind),
        operatorDependency:
          parsed.operatorDependency === "low"
            ? "low"
            : parsed.operatorDependency === "medium"
              ? "medium"
              : parsed.operatorDependency === "high"
                ? "high"
                : ("unknown" as OperatorDependency),
        includePrompts: parsed.includePrompts !== false,
        includeManual: parsed.includeManual !== false,
      });
    } catch {
      setDraft(defaultDraft());
    }
  }, [storageKey]);

  useEffect(() => {
    safeSet(storageKey, JSON.stringify(draft));
    const code = selectedChannel ?? channelFromQuery ?? null;
    if (code) {
      safeSet("ui.youtubeSale.lastChannel", code);
    }
  }, [channelFromQuery, draft, selectedChannel, storageKey]);

  const channelOptions = useMemo(() => {
    return [...channels].sort((a, b) => compareChannelCode(a.code, b.code));
  }, [channels]);

  const selectedChannelSummary = useMemo(() => {
    const code = selectedChannel ?? channelFromQuery ?? null;
    if (!code) return null;
    return channels.find((c) => c.code === code) ?? null;
  }, [channelFromQuery, channels, selectedChannel]);

  const selectedBranding = selectedChannelSummary?.branding ?? null;
  const youtubeMetricsAvailable = Boolean(
    selectedBranding &&
      (selectedBranding.subscriber_count ||
        selectedBranding.view_count ||
        selectedBranding.video_count ||
        (selectedBranding.url || "").trim() ||
        (selectedBranding.launch_date || "").trim())
  );

  const handleChannelChange = useCallback(
    (event: React.ChangeEvent<HTMLSelectElement>) => {
      const code = (event.target.value || "").trim().toUpperCase();
      selectChannel(code || null);
      const params = new URLSearchParams(location.search);
      if (code) {
        params.set("channel", code);
      } else {
        params.delete("channel");
      }
      const search = params.toString();
      navigate({ pathname: location.pathname, search: search ? `?${search}` : "" }, { replace: true });
    },
    [location.pathname, location.search, navigate, selectChannel]
  );

  const salePriceYen = useMemo(() => parsePositiveInt(draft.salePriceYen), [draft.salePriceYen]);
  const fees = useMemo(() => (salePriceYen ? calcRaccoFees(salePriceYen, draft.listingMode) : null), [draft.listingMode, salePriceYen]);
  const minFeeThresholdYen = useMemo(() => Math.round(55_000 / 0.05), []);
  const monthlyProfitYen = useMemo(() => parsePositiveInt(draft.monthlyProfitYen), [draft.monthlyProfitYen]);
  const paybackMonths = useMemo(() => {
    if (!salePriceYen || !monthlyProfitYen) return null;
    if (monthlyProfitYen <= 0) return null;
    const raw = salePriceYen / monthlyProfitYen;
    return Math.round(raw * 10) / 10;
  }, [monthlyProfitYen, salePriceYen]);

  const dossierParams = useMemo<SaleDossierParams>(
    () => ({ channel: selectedChannelSummary, channelCode: selectedChannel ?? channelFromQuery ?? null, draft }),
    [channelFromQuery, draft, selectedChannel, selectedChannelSummary]
  );

  const generatedDocs = useMemo<Template[]>(
    () => [
      {
        key: "racco-listing",
        title: "案件掲載文（ラッコM&A貼り付け用）",
        desc: "案件説明の叩き台。数字/権利/属人性だけ埋めれば出せる形に固定。",
        body: generateRaccoListingText(dossierParams),
      },
      {
        key: "buyer-message",
        title: "買主への案内文（初回メッセージ）",
        desc: "招待→7日→メインオーナー移行、AdSense差し替え、検収の流れを短く。",
        body: generateBuyerMessageText(dossierParams),
      },
      {
        key: "acceptance",
        title: "検収条件テンプレ",
        desc: "揉めやすい部分だけ先に固定（権限/AdSense/提供物/期間）。",
        body: generateAcceptanceCriteriaText(dossierParams),
      },
      {
        key: "delivery-checklist",
        title: "納品チェックリスト",
        desc: "やり忘れ防止。チェックボックス形式（Markdown）。",
        body: generateDeliveryChecklistText(dossierParams),
      },
      {
        key: "handover-min-pack",
        title: "買主に渡す最小パック（要旨）",
        desc: "この自動化システムは渡さない前提。最低限の「運用できる状態」だけ作る。",
        body: [
          "【買主に渡す最小パック】",
          "1) 引き継ぎ手順書（ブランドアカウント権限移行 / AdSense差し替え / 検収条件）",
          "2) 運用マニュアル（企画→台本→制作→投稿→改善の型だけ）",
          "3) 台本作成プロンプト（テンプレ）",
          "4) サムネ作成プロンプト（テンプレ）",
          "5) 直近の改善メモ（伸びた企画/ダメだった企画を箇条書きで）",
        ].join("\n"),
      },
      {
        key: "script-prompt",
        title: "台本作成プロンプト（買主向けテンプレ）",
        desc: "買主が“そのまま量産”できる最低限の台本テンプレ。",
        body: [
          "あなたはYouTube台本のプロの構成作家です。",
          "",
          "【チャンネル前提】",
          "- ジャンル/テーマ: {例: 雑学/時事/歴史/健康/金融など}",
          "- 視聴者像: {年齢/性別/悩み/知識レベル}",
          "- 動画尺: {例: 8〜12分}",
          "- トーン: {例: 丁寧/テンポ速め/落ち着き}",
          "",
          "【今回のテーマ】",
          "- タイトルの種: {テーマ}",
          "- 伝えたい結論: {結論}",
          "- 絶対に避けたい表現: {例: 断定的な医療助言、差別、誹謗中傷}",
          "",
          "【出力してほしいもの】",
          "1) タイトル案10個（誇張しない）",
          "2) サムネ文字案5個（2〜4語、強い名詞で）",
          "3) 冒頭0〜15秒（結論先出し→疑問提示→続き導線）",
          "4) 全体の章立て（見出し+各章の要点）",
          "5) 台本本文（口語/短文/読み上げやすい）",
          "6) 各章の「テロップ案/図解案/B-roll案」各3つ",
          "7) 最後に「事実確認が必要な箇所」リスト（出典候補キーワード付き）",
          "",
          "【品質条件】",
          "- 各章の冒頭で“次を見るメリット”を1行入れる",
          "- 具体例を最低3つ入れる",
          "- 結論→理由→具体→まとめの順を崩さない",
        ].join("\n"),
      },
      {
        key: "thumb-prompt",
        title: "サムネ画像作成プロンプト（買主向けテンプレ）",
        desc: "画像生成→文字入れの分離前提（文字は後入れ）。",
        body: [
          "【デザイン指示】",
          "- 0.5秒で内容が伝わる / 文字は少なく太く",
          "- 構図: 主役1つ + 大きい文字（2〜4語）",
          "- 色: 高コントラスト（例: 黄×黒 / 白×赤）",
          "- 禁止: 細かい文章、要素の詰め込み、小さすぎる文字",
          "",
          "【画像生成プロンプト（汎用）】",
          "{テーマ}を直感的に表す、強いコントラストのYouTubeサムネ。",
          "主役は1つ、背景はシンプル。",
          "感情は{驚き/納得/不安/希望}。",
          "16:9、超高精細。",
          "文字入れは後で行うので、画像に文字は入れない。",
          "",
          "（ネガティブ）ごちゃごちゃ、細かい文字、ロゴ、透かし。",
        ].join("\n"),
      },
    ],
    [dossierParams]
  );

  const combinedDoc = useMemo(() => {
    const parts: string[] = [];
    parts.push("【YouTube売却 Dossier（自動生成）】");
    parts.push("");
    for (const doc of generatedDocs) {
      parts.push(`---\n\n# ${doc.title}\n`);
      parts.push(doc.body.trim());
      parts.push("");
    }
    return parts.join("\n").trim() + "\n";
  }, [generatedDocs]);

  return (
    <section className="research-workspace research-workspace--wide">
      <header className="research-workspace__header">
        <div>
          <p className="eyebrow">/youtube-sale</p>
          <h2>YouTube売却（RaccoM&amp;A）</h2>
          <p className="research-workspace__note">
            YouTubeチャンネル売却のための「最小チェック」と「買主に渡すテンプレ」を固定します（社内用）。システム/内部リポジトリは提供しない前提です。
          </p>
          {copyStatus ? (
            <div className="main-alert" style={{ marginTop: 10 }}>
              {copyStatus}
            </div>
          ) : null}
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 10 }}>
            <a className="research-chip" href="https://rakkoma.com/knowledge/12167/" target="_blank" rel="noreferrer">
              ラッコM&amp;A: 売却の流れ
            </a>
            <a className="research-chip" href="https://rakkoma.com/knowledge/3704/" target="_blank" rel="noreferrer">
              ラッコM&amp;A: 手数料
            </a>
            <a className="research-chip" href="https://rakkoma.com/knowledge/5809/" target="_blank" rel="noreferrer">
              YouTube引き継ぎ（ブランドアカウント）
            </a>
            <a className="research-chip" href="https://rakkoma.com/knowledge/5928/" target="_blank" rel="noreferrer">
              AdSense引き継ぎ（不可）
            </a>
          </div>
        </div>
      </header>

      <section style={{ border: "1px solid var(--color-border-muted)", borderRadius: 14, overflow: "hidden", marginTop: 14 }}>
        <header style={{ padding: 12, borderBottom: "1px solid var(--color-border-muted)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
            <strong>案件入力（下書きはブラウザに自動保存）</strong>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <button
                type="button"
                className="workspace-button"
                onClick={() => {
                  safeRemove(storageKey);
                  setDraft(defaultDraft());
                  setCopyStatus("下書きをリセットしました");
                  window.setTimeout(() => setCopyStatus(null), 2500);
                }}
              >
                リセット
              </button>
              {youtubeMetricsAvailable ? (
                <button
                  type="button"
                  className="workspace-button"
                  onClick={() => {
                    if (!selectedBranding) return;
                    setDraft((prev) => ({
                      ...prev,
                      subscribers:
                        prev.subscribers.trim() || !selectedBranding.subscriber_count
                          ? prev.subscribers
                          : String(selectedBranding.subscriber_count),
                    }));
                    setCopyStatus("YouTubeメトリクス（登録者）を反映しました");
                    window.setTimeout(() => setCopyStatus(null), 2500);
                  }}
                >
                  YouTubeメトリクス反映
                </button>
              ) : null}
            </div>
          </div>
        </header>

        <div style={{ padding: 12, background: "var(--color-surface)" }}>
          {channelsLoading || channelsError ? (
            <div className="main-alert" style={{ marginBottom: 12 }}>
              {channelsLoading ? "チャンネル読み込み中…" : null}
              {channelsError ? `エラー: ${channelsError}` : null}
            </div>
          ) : null}

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 12 }}>
            <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, color: "#475569" }}>
              対象チャンネル
              <select
                value={selectedChannel ?? channelFromQuery ?? ""}
                onChange={handleChannelChange}
                style={{
                  border: "1px solid rgba(148, 163, 184, 0.6)",
                  borderRadius: 10,
                  padding: "6px 10px",
                  fontSize: 13,
                  background: "#fff",
                }}
              >
                <option value="">（選択なし）</option>
                {channelOptions.map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.code} · {resolveChannelDisplayName(c)}
                    {c.youtube_handle ? ` (@${c.youtube_handle})` : ""}
                  </option>
                ))}
              </select>
            </label>

            <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, color: "#475569" }}>
              売却価格（税込・円）
              <input
                type="text"
                inputMode="numeric"
                value={draft.salePriceYen}
                onChange={(e) => setDraft((prev) => ({ ...prev, salePriceYen: e.target.value }))}
                placeholder="例: 690000"
                style={{ border: "1px solid rgba(148, 163, 184, 0.6)", borderRadius: 10, padding: "6px 10px", fontSize: 13 }}
              />
            </label>

            <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, color: "#475569" }}>
              掲載形態
              <select
                value={draft.listingMode}
                onChange={(e) => setDraft((prev) => ({ ...prev, listingMode: e.target.value === "closed" ? "closed" : "normal" }))}
                style={{
                  border: "1px solid rgba(148, 163, 184, 0.6)",
                  borderRadius: 10,
                  padding: "6px 10px",
                  fontSize: 13,
                  background: "#fff",
                }}
              >
                <option value="normal">通常（売主手数料0円）</option>
                <option value="closed">クローズド（売主も手数料あり）</option>
              </select>
            </label>

            <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, color: "#475569" }}>
              アカウント形態（目安）
              <select
                value={draft.ownership}
                onChange={(e) =>
                  setDraft((prev) => ({
                    ...prev,
                    ownership: e.target.value === "brand" ? "brand" : e.target.value === "personal" ? "personal" : "unknown",
                  }))
                }
                style={{
                  border: "1px solid rgba(148, 163, 184, 0.6)",
                  borderRadius: 10,
                  padding: "6px 10px",
                  fontSize: 13,
                  background: "#fff",
                }}
              >
                <option value="unknown">不明</option>
                <option value="brand">ブランドアカウント</option>
                <option value="personal">個人アカウント</option>
              </select>
            </label>

            <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, color: "#475569" }}>
              属人性（目安）
              <select
                value={draft.operatorDependency}
                onChange={(e) =>
                  setDraft((prev) => ({
                    ...prev,
                    operatorDependency:
                      e.target.value === "low"
                        ? "low"
                        : e.target.value === "medium"
                          ? "medium"
                          : e.target.value === "high"
                            ? "high"
                            : "unknown",
                  }))
                }
                style={{
                  border: "1px solid rgba(148, 163, 184, 0.6)",
                  borderRadius: 10,
                  padding: "6px 10px",
                  fontSize: 13,
                  background: "#fff",
                }}
              >
                <option value="unknown">不明</option>
                <option value="low">低（誰でも回せる）</option>
                <option value="medium">中</option>
                <option value="high">高（声/顔/固有ノウハウ依存）</option>
              </select>
            </label>

            <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, color: "#475569" }}>
              利益目安（円/月）
              <input
                type="text"
                inputMode="numeric"
                value={draft.monthlyProfitYen}
                onChange={(e) => setDraft((prev) => ({ ...prev, monthlyProfitYen: e.target.value }))}
                placeholder="例: 120000"
                style={{ border: "1px solid rgba(148, 163, 184, 0.6)", borderRadius: 10, padding: "6px 10px", fontSize: 13 }}
              />
            </label>

            <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, color: "#475569" }}>
              直近何ヶ月（平均の根拠）
              <input
                type="text"
                inputMode="numeric"
                value={draft.avgMonths}
                onChange={(e) => setDraft((prev) => ({ ...prev, avgMonths: e.target.value }))}
                placeholder="例: 6"
                style={{ border: "1px solid rgba(148, 163, 184, 0.6)", borderRadius: 10, padding: "6px 10px", fontSize: 13 }}
              />
            </label>

            <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, color: "#475569" }}>
              登録者（任意）
              <input
                type="text"
                inputMode="numeric"
                value={draft.subscribers}
                onChange={(e) => setDraft((prev) => ({ ...prev, subscribers: e.target.value }))}
                placeholder="例: 35000"
                style={{ border: "1px solid rgba(148, 163, 184, 0.6)", borderRadius: 10, padding: "6px 10px", fontSize: 13 }}
              />
            </label>

            <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, color: "#475569" }}>
              月間再生（任意）
              <input
                type="text"
                inputMode="numeric"
                value={draft.monthlyViews}
                onChange={(e) => setDraft((prev) => ({ ...prev, monthlyViews: e.target.value }))}
                placeholder="例: 2500000"
                style={{ border: "1px solid rgba(148, 163, 184, 0.6)", borderRadius: 10, padding: "6px 10px", fontSize: 13 }}
              />
            </label>
          </div>

          {youtubeMetricsAvailable ? (
            <div style={{ marginTop: 12, border: "1px solid var(--color-border-muted)", borderRadius: 12, padding: 10, background: "#fff" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
                <strong style={{ fontSize: 13 }}>YouTubeメトリクス（自動取得）</strong>
                <span className="muted small-text">
                  updated: {selectedBranding?.updated_at ? new Date(selectedBranding.updated_at).toLocaleString("ja-JP") : "—"}
                </span>
              </div>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 8 }}>
                {selectedBranding?.subscriber_count ? (
                  <span className="badge subtle">登録者: {formatCount(selectedBranding.subscriber_count)} 人</span>
                ) : null}
                {selectedBranding?.view_count ? (
                  <span className="badge subtle">総再生: {formatCount(selectedBranding.view_count)} 回</span>
                ) : null}
                {selectedBranding?.video_count ? (
                  <span className="badge subtle">動画数: {formatCount(selectedBranding.video_count)} 本</span>
                ) : null}
                {selectedBranding?.launch_date ? <span className="badge subtle">開設: {selectedBranding.launch_date}</span> : null}
                {selectedBranding?.url ? (
                  <a className="badge subtle" href={selectedBranding.url} target="_blank" rel="noreferrer">
                    チャンネルURL
                  </a>
                ) : null}
              </div>
              <div className="muted small-text" style={{ marginTop: 6, lineHeight: 1.6 }}>
                ※ここは固定のモックではなく、チャンネルレジストリ（YouTubeメトリクス）からの取得値です。
              </div>
            </div>
          ) : null}

          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 12 }}>
            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, color: "#334155" }}>
              <input
                type="checkbox"
                checked={draft.includePrompts}
                onChange={(e) => setDraft((prev) => ({ ...prev, includePrompts: e.target.checked }))}
              />
              プロンプト（台本/サムネ）を渡す
            </label>
            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, color: "#334155" }}>
              <input
                type="checkbox"
                checked={draft.includeManual}
                onChange={(e) => setDraft((prev) => ({ ...prev, includeManual: e.target.checked }))}
              />
              運用マニュアルを渡す
            </label>
            <span className="badge subtle">システム/内部ツールは渡さない</span>
          </div>

          <div style={{ display: "grid", gap: 12, marginTop: 12 }}>
            <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, color: "#475569" }}>
              権利/注意事項（任意）
              <textarea
                value={draft.rightsNote}
                onChange={(e) => setDraft((prev) => ({ ...prev, rightsNote: e.target.value }))}
                placeholder="例: BGMは全て商用利用OKのライセンス、素材は自作中心…など"
                rows={3}
                style={{
                  border: "1px solid rgba(148, 163, 184, 0.6)",
                  borderRadius: 10,
                  padding: "8px 10px",
                  fontSize: 13,
                  resize: "vertical",
                }}
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, color: "#475569" }}>
              補足メモ（任意）
              <textarea
                value={draft.customNote}
                onChange={(e) => setDraft((prev) => ({ ...prev, customNote: e.target.value }))}
                placeholder="例: 引き継ぎは平日夜のみ対応…など"
                rows={2}
                style={{
                  border: "1px solid rgba(148, 163, 184, 0.6)",
                  borderRadius: 10,
                  padding: "8px 10px",
                  fontSize: 13,
                  resize: "vertical",
                }}
              />
            </label>
          </div>
        </div>
      </section>

      <div className="research-quick" style={{ marginTop: 12, lineHeight: 1.8 }}>
        <div style={{ fontWeight: 950 }}>絶対に詰まらないための最小チェック</div>
        <ul style={{ margin: "8px 0 0 0", paddingLeft: 18 }}>
          <li>
            チャンネルが <span className="badge subtle">ブランドアカウント</span> 前提で譲渡できる状態か（権限移行がボトルネック）。
          </li>
          <li>
            権限移行は「買主をオーナー招待 → <span className="badge subtle">7日</span> 後にメインのオーナー → 売主削除」が基本線（スケジュールに組み込む）。
          </li>
          <li>AdSenseは譲渡不可のため、買主側AdSenseへ差し替え（検収条件・収益区切りを事前合意）。</li>
          <li>収益/費用/利益の根拠（直近6〜12ヶ月）と、権利関係（素材・BGM・転載等）を出せる状態にする。</li>
        </ul>
      </div>

      <section style={{ border: "1px solid var(--color-border-muted)", borderRadius: 14, overflow: "hidden", marginTop: 14 }}>
        <header style={{ padding: 12, borderBottom: "1px solid var(--color-border-muted)" }}>
          <strong>手数料・金額感（ラッコM&amp;A）</strong>
          <div className="muted small-text" style={{ marginTop: 6, lineHeight: 1.6 }}>
            目安: 買主手数料は 5%（最低55,000円）。5%が最低額を上回るのは <span className="mono">{formatYen(minFeeThresholdYen)}円</span>{" "}
            以上。
          </div>
        </header>
        <div style={{ padding: 12, background: "var(--color-surface)" }}>
          {!salePriceYen ? (
            <div className="main-alert main-alert--error">売却価格（税込・円）を入力してください。</div>
          ) : fees ? (
            <div style={{ display: "grid", gap: 10 }}>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <span className="badge subtle">掲載: {fees.listingMode === "closed" ? "クローズド" : "通常"}</span>
                <span className="badge subtle">売却価格: {formatYen(fees.salePriceYen)}円</span>
                <span className="badge subtle">買主手数料: {formatYen(fees.buyerFeeYen)}円</span>
                {fees.sellerFeeYen ? <span className="badge subtle">売主手数料: {formatYen(fees.sellerFeeYen)}円</span> : null}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 10 }}>
                <div style={{ border: "1px solid var(--color-border-muted)", borderRadius: 12, padding: 10, background: "#fff" }}>
                  <div className="muted small-text">買主の支払総額（目安）</div>
                  <div style={{ fontSize: 22, fontWeight: 900, marginTop: 4 }}>{formatYen(fees.buyerPaysYen)}円</div>
                </div>
                <div style={{ border: "1px solid var(--color-border-muted)", borderRadius: 12, padding: 10, background: "#fff" }}>
                  <div className="muted small-text">売主の受取額（目安）</div>
                  <div style={{ fontSize: 22, fontWeight: 900, marginTop: 4 }}>{formatYen(fees.sellerReceivesYen)}円</div>
                </div>
                {paybackMonths ? (
                  <div style={{ border: "1px solid var(--color-border-muted)", borderRadius: 12, padding: 10, background: "#fff" }}>
                    <div className="muted small-text">回収期間（目安）</div>
                    <div style={{ fontSize: 22, fontWeight: 900, marginTop: 4 }}>{paybackMonths} ヶ月</div>
                    <div className="muted small-text" style={{ marginTop: 4 }}>
                      前提: 月の利益 = {formatYen(monthlyProfitYen ?? 0)}円
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}
        </div>
      </section>

      <section style={{ border: "1px solid var(--color-border-muted)", borderRadius: 14, overflow: "hidden", marginTop: 14 }}>
        <header style={{ padding: 12, borderBottom: "1px solid var(--color-border-muted)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
            <strong>Dossier（買主/案件向け文面の生成）</strong>
            <button
              type="button"
              className="workspace-button workspace-button--primary"
              onClick={() => {
                void copyToClipboard(combinedDoc).then((ok) => {
                  setCopyStatus(ok ? "コピーしました: Dossier（全部）" : "コピー失敗: Dossier（全部）");
                  window.setTimeout(() => setCopyStatus(null), 2500);
                });
              }}
            >
              全部まとめてコピー
            </button>
          </div>
        </header>
        <div style={{ padding: 12, background: "var(--color-surface)" }}>
          <div style={{ display: "grid", gap: 16 }}>
            {generatedDocs.map((t) => (
              <section key={t.key} style={{ border: "1px solid var(--color-border-muted)", borderRadius: 14, overflow: "hidden" }}>
                <header style={{ padding: 12, borderBottom: "1px solid var(--color-border-muted)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
                    <strong>{t.title}</strong>
                    <button
                      type="button"
                      className="workspace-button"
                      onClick={() => {
                        void copyToClipboard(t.body).then((ok) => {
                          setCopyStatus(ok ? `コピーしました: ${t.title}` : `コピー失敗: ${t.title}`);
                          window.setTimeout(() => setCopyStatus(null), 2500);
                        });
                      }}
                    >
                      コピー
                    </button>
                  </div>
                  <div className="muted small-text" style={{ marginTop: 6 }}>
                    {t.desc}
                  </div>
                </header>
                <div style={{ padding: 12, background: "#fff" }}>
                  <pre className="mono" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
                    {t.body}
                  </pre>
                </div>
              </section>
            ))}
          </div>
        </div>
      </section>

    </section>
  );
}

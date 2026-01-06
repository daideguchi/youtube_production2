import { useMemo, useState } from "react";
import { useOutletContext } from "react-router-dom";
import type { ShellOutletContext } from "../layouts/AppShell";
import "./PublishingProgressPage.css";

type RunwayFilter = "all" | "depleted" | "short" | "ok";

type ParsedScheduleItem = {
  date: string; // YYYY-MM-DD
  kind: "scheduled" | "published" | "unknown";
  title: string;
  label?: string;
};

type ChannelRunway = {
  channel: string; // CH06
  channelName: string;
  lastPublishedDate?: string | null; // YYYY-MM-DD
  lastScheduledDate?: string | null; // YYYY-MM-DD
  scheduleRunwayDays: number;
  upcomingCount: number | null;
  upcoming: ParsedScheduleItem[];
  allItems: ParsedScheduleItem[];
};

type ParseResult = {
  baseDateFromText?: string | null; // YYYY-MM-DD
  channels: ChannelRunway[];
  warnings: string[];
  errors: string[];
};

const STORAGE_RAW_KEY = "ui.publishing_progress.raw_v1";
const STORAGE_BASEDATE_KEY = "ui.publishing_progress.base_date_v1";
const STORAGE_BASEDATE_MANUAL_KEY = "ui.publishing_progress.base_date_manual_v1";

function jstTodayIso(): string {
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return fmt.format(new Date());
}

function normalizeDateCell(value: string): string | null {
  const v = (value ?? "").trim();
  if (!v) return null;
  if (v === "なし" || v === "無し" || v === "—" || v === "－") return null;
  const m = v.match(/(\d{4})[/-](\d{1,2})[/-](\d{1,2})/);
  if (!m) return null;
  const mm = String(m[2]).padStart(2, "0");
  const dd = String(m[3]).padStart(2, "0");
  return `${m[1]}-${mm}-${dd}`;
}

function normalizeChannelCode(value: string): string | null {
  const raw = (value ?? "").trim();
  if (!raw) return null;
  const m = raw.match(/ch\s*(\d+)/i) || raw.match(/^(\d+)$/);
  if (!m) return null;
  const digits = String(m[1] ?? "").trim();
  if (!digits) return null;
  return `CH${digits.padStart(2, "0")}`;
}

function diffDaysJst(aIso: string, bIso: string): number {
  const a = new Date(`${aIso}T00:00:00+09:00`).getTime();
  const b = new Date(`${bIso}T00:00:00+09:00`).getTime();
  return Math.round((a - b) / 86400000);
}

function formatYmd(value?: string | null): string {
  const iso = (value ?? "").trim();
  if (!iso) return "—";
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (m) return `${m[1]}/${m[2]}/${m[3]}`;
  return iso;
}

function runwayTone(days: number): "danger" | "warn" | "ok" | "muted" {
  if (days <= 0) return "danger";
  if (days <= 3) return "warn";
  return "ok";
}

function channelMatches(query: string, channel: { code: string; name?: string | null }): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  if (channel.code.toLowerCase().includes(q)) return true;
  return (channel.name ?? "").toLowerCase().includes(q);
}

function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i] ?? "";
    if (ch === '"') {
      const next = line[i + 1];
      if (inQuotes && next === '"') {
        cur += '"';
        i += 1;
        continue;
      }
      inQuotes = !inQuotes;
      continue;
    }
    if (ch === "," && !inQuotes) {
      out.push(cur.trim());
      cur = "";
      continue;
    }
    cur += ch;
  }
  out.push(cur.trim());
  return out;
}

function parseStudioRunwayText(text: string, channelNameByCode: Map<string, string>): ParseResult {
  const warnings: string[] = [];
  const errors: string[] = [];

  const baseMatch =
    text.match(/(\d{4}[/-]\d{1,2}[/-]\d{1,2}).{0,24}時点/) ||
    text.match(/時点.{0,24}(\d{4}[/-]\d{1,2}[/-]\d{1,2})/);
  const baseDateFromText = baseMatch ? normalizeDateCell(baseMatch[1] ?? baseMatch[0]) : null;

  const lines = text.split(/\r?\n/);
  const tableByChannel = new Map<
    string,
    { name?: string; lastPublished?: string | null; lastScheduled?: string | null }
  >();

  for (let idx = 0; idx < lines.length; idx++) {
    const raw = (lines[idx] ?? "").trim();
    if (!raw) continue;
    const lower = raw.toLowerCase();
    if (!(lower.includes("channel_id") && lower.includes("last_scheduled"))) continue;
    const headerCells = splitCsvLine(raw).map((c) => c.trim().toLowerCase());
    const headerIndex = new Map<string, number>();
    headerCells.forEach((h, i) => headerIndex.set(h, i));
    const getIdx = (key: string) => headerIndex.get(key) ?? -1;
    const channelIdIdx = getIdx("channel_id");
    const channelNameIdx = getIdx("channel_name");
    const lastPublishedIdx = getIdx("last_published_date");
    const lastScheduledIdx = getIdx("last_scheduled_date");

    if (channelIdIdx < 0) continue;
    for (let rowIdx = idx + 1; rowIdx < lines.length; rowIdx++) {
      const row = (lines[rowIdx] ?? "").trim();
      if (!row) continue;
      if (row.startsWith("```")) break;
      const cells = splitCsvLine(row);
      const code = normalizeChannelCode(cells[channelIdIdx] ?? "");
      if (!code) continue;
      const name = (cells[channelNameIdx] ?? "").trim();
      const lastPublished = normalizeDateCell(cells[lastPublishedIdx] ?? "");
      const lastScheduled = normalizeDateCell(cells[lastScheduledIdx] ?? "");
      tableByChannel.set(code, {
        name: name || channelNameByCode.get(code) || code,
        lastPublished,
        lastScheduled,
      });
    }
    break;
  }

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed.startsWith("|")) continue;
    if (trimmed.includes("---")) continue;
    const cells = trimmed
      .split("|")
      .map((c) => c.trim())
      .filter(Boolean);
    if (cells.length < 4) continue;
    const code = normalizeChannelCode(cells[0]);
    if (!code) continue;
    const name = (cells[1] ?? "").trim();
    const lastPublished = normalizeDateCell(cells[2] ?? "");
    const rawScheduled = (cells[3] ?? "").trim();
    const lastScheduled = normalizeDateCell(rawScheduled);
    tableByChannel.set(code, {
      name: name || channelNameByCode.get(code) || code,
      lastPublished,
      lastScheduled,
    });
  }

  const listItemsByChannel = new Map<string, ParsedScheduleItem[]>();
  let currentChannel: string | null = null;
  for (const line of lines) {
    const heading = line.match(/^###\s*(ch\s*\d+)\s*[:：]/i);
    if (heading) {
      currentChannel = normalizeChannelCode(heading[1] ?? "") ?? null;
      continue;
    }
    if (!currentChannel) continue;

    const row = line.match(/\*\s*\*\*(\d{4}[/-]\d{1,2}[/-]\d{1,2})(?:[（(]([^）)]+)[）)])?\*\*\s*[:：]\s*(.+)$/);
    if (row) {
      const iso = normalizeDateCell(row[1] ?? "");
      const label = (row[2] ?? "").trim();
      const title = (row[3] ?? "").trim();
      if (iso && title) {
        const kind: ParsedScheduleItem["kind"] = label.includes("公開予約")
          ? "scheduled"
          : label.includes("公開")
          ? "published"
          : "unknown";
        const items = listItemsByChannel.get(currentChannel) ?? [];
        items.push({ date: iso, kind, title, label: label || undefined });
        listItemsByChannel.set(currentChannel, items);
      }
      continue;
    }

    const lastPub = line.match(/直近の公開最終.*?(\d{4}[/-]\d{1,2}[/-]\d{1,2})[:：]\s*(.+)$/);
    if (lastPub) {
      const iso = normalizeDateCell(lastPub[1] ?? "");
      const title = (lastPub[2] ?? "").trim();
      if (iso && title) {
        const items = listItemsByChannel.get(currentChannel) ?? [];
        items.push({ date: iso, kind: "published", title, label: "直近の公開最終" });
        listItemsByChannel.set(currentChannel, items);
      }
    }
  }

  const channelCodeIndex: Record<string, true> = {};
  tableByChannel.forEach((_v, code) => {
    channelCodeIndex[code] = true;
  });
  listItemsByChannel.forEach((_v, code) => {
    channelCodeIndex[code] = true;
  });

  const channelCodes = Object.keys(channelCodeIndex);
  const channels: ChannelRunway[] = [];

  for (const code of channelCodes) {
    const table = tableByChannel.get(code);
    const listItems = (listItemsByChannel.get(code) ?? []).slice();
    listItems.sort((a, b) => a.date.localeCompare(b.date));

    const listPublished = listItems.filter((it) => it.kind === "published").map((it) => it.date);
    const listScheduled = listItems.filter((it) => it.kind === "scheduled").map((it) => it.date);

    const lastPublished =
      table?.lastPublished ?? (listPublished.length ? listPublished[listPublished.length - 1] : null);
    const lastScheduled =
      table?.lastScheduled ?? (listScheduled.length ? listScheduled[listScheduled.length - 1] : null);

    const name = table?.name || channelNameByCode.get(code) || code;
    channels.push({
      channel: code,
      channelName: name,
      lastPublishedDate: lastPublished,
      lastScheduledDate: lastScheduled,
      scheduleRunwayDays: 0,
      upcomingCount: 0,
      upcoming: [],
      allItems: listItems,
    });
  }

  if (!channels.length) {
    errors.push(
      "貼り付け内容からチャンネル情報を解析できませんでした。表（| ch06 | ...）か、各チャンネル見出し（### ch06：...）を含めてください。"
    );
  }

  channels.sort((a, b) => a.channel.localeCompare(b.channel));
  return { baseDateFromText, channels, warnings, errors };
}

export function PublishingProgressPage() {
  const { channels } = useOutletContext<ShellOutletContext>();

  const channelNameByCode = useMemo(() => {
    const map = new Map<string, string>();
    for (const ch of channels) {
      const code = (ch.code ?? "").trim().toUpperCase();
      if (!code) continue;
      map.set(code, (ch.name ?? "").trim());
    }
    return map;
  }, [channels]);

  const [rawText, setRawText] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_RAW_KEY) ?? "";
    } catch {
      return "";
    }
  });
  const [baseDateIso, setBaseDateIso] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_BASEDATE_KEY) ?? jstTodayIso();
    } catch {
      return jstTodayIso();
    }
  });
  const [baseDateManual, setBaseDateManual] = useState(() => {
    try {
      return (localStorage.getItem(STORAGE_BASEDATE_MANUAL_KEY) ?? "") === "1";
    } catch {
      return false;
    }
  });

  const [parseResult, setParseResult] = useState<ParseResult>(() => parseStudioRunwayText(rawText, channelNameByCode));

  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<RunwayFilter>("all");
  const [upcomingLimit, setUpcomingLimit] = useState<number>(12);

  const computedChannels = useMemo(() => {
    const base = baseDateIso || jstTodayIso();
    return (parseResult.channels ?? []).map((ch) => {
      const lastScheduled = ch.lastScheduledDate ? String(ch.lastScheduledDate) : null;
      const runwayDays = lastScheduled ? Math.max(0, diffDaysJst(lastScheduled, base)) : 0;

      const scheduledItems = ch.allItems
        .filter((it) => it.kind === "scheduled")
        .slice()
        .sort((a, b) => a.date.localeCompare(b.date));

      const hasScheduledDetails = scheduledItems.length > 0;
      const upcoming = hasScheduledDetails ? scheduledItems.filter((it) => it.date >= base) : [];
      const upcomingCount =
        hasScheduledDetails ? upcoming.length : lastScheduled && diffDaysJst(lastScheduled, base) >= 0 ? null : 0;
      const limitedUpcoming = upcoming.slice(0, Math.max(0, upcomingLimit));

      return {
        ...ch,
        scheduleRunwayDays: runwayDays,
        upcomingCount,
        upcoming: limitedUpcoming,
      };
    });
  }, [parseResult.channels, baseDateIso, upcomingLimit]);

  const stats = useMemo(() => {
    const items = computedChannels;
    const depleted = items.filter((it) => it.scheduleRunwayDays <= 0).length;
    const short = items.filter((it) => it.scheduleRunwayDays >= 1 && it.scheduleRunwayDays <= 3).length;
    const ok = items.filter((it) => it.scheduleRunwayDays >= 4).length;
    return { total: items.length, depleted, short, ok };
  }, [computedChannels]);

  const visibleChannels = useMemo(() => {
    const enriched = computedChannels.map((it) => ({
      ...it,
      _code: it.channel.toUpperCase(),
      _name: it.channelName || channelNameByCode.get(it.channel) || it.channel,
    }));

    const filtered = enriched.filter((it) => {
      const matches = channelMatches(search, { code: it._code, name: it._name });
      if (!matches) return false;
      if (filter === "depleted") return it.scheduleRunwayDays <= 0;
      if (filter === "short") return it.scheduleRunwayDays >= 1 && it.scheduleRunwayDays <= 3;
      if (filter === "ok") return it.scheduleRunwayDays >= 4;
      return true;
    });

    filtered.sort((a, b) => {
      if (a.scheduleRunwayDays !== b.scheduleRunwayDays) return a.scheduleRunwayDays - b.scheduleRunwayDays;
      return a._code.localeCompare(b._code);
    });

    return filtered;
  }, [computedChannels, search, filter, channelNameByCode]);

  const headerText = useMemo(() => {
    const base = baseDateIso || jstTodayIso();
    return `OAuth不要：YouTube Studio の一覧（抜粋）を貼り付けて可視化します · 基準日(JST): ${formatYmd(base)}`;
  }, [baseDateIso]);

  const handleParse = () => {
    try {
      localStorage.setItem(STORAGE_RAW_KEY, rawText);
    } catch {
      // ignore
    }
    const parsed = parseStudioRunwayText(rawText, channelNameByCode);
    setParseResult(parsed);
    if (!baseDateManual && parsed.baseDateFromText) {
      setBaseDateIso(parsed.baseDateFromText);
      try {
        localStorage.setItem(STORAGE_BASEDATE_KEY, parsed.baseDateFromText);
      } catch {
        // ignore
      }
    }
  };

  const handleClear = () => {
    setRawText("");
    setParseResult(parseStudioRunwayText("", channelNameByCode));
    try {
      localStorage.removeItem(STORAGE_RAW_KEY);
    } catch {
      // ignore
    }
  };

  const handlePasteSample = () => {
    const sample = `## 結論サマリ（いつまで“完了 / 予約”できているか）\n\n| ch   | チャンネル名       | 投稿完了（公開）の最終日 | 投稿予約（公開予約）の最終日 | 予約の残り日数* | 備考 |\n| ---- | ------------ | -----------: | -------------: | -------: | ----------------------------- |\n| ch04 | 隠れ書庫アカシック    |   2025/12/31 |             なし |        0 |  |\n| ch02 | 静寂の哲学        |   2026/01/05 |             なし |        0 |  |\n| ch22 | シニアの心を軽くする物語 |   2026/01/05 |     2026/01/07 |        1 |  |\n| ch06 | 都市伝説のダーク図書館  |   2026/01/05 |     2026/01/13 |        7 |  |\n| ch24 | 叡智の扉         |   2026/01/05 |     2026/01/09 |        3 |  |\n\n### ch06：都市伝説のダーク図書館\n* **2026/01/06（公開予約）**：【痕跡なき大爆発】ツングースカ - 正体不明の空中爆発\n* **2026/01/13（公開予約）**：【ピラミッドより古い神殿】ギョベクリ・テペ―歴史が崩れる瞬間\n`;
    setRawText(sample);
    try {
      localStorage.setItem(STORAGE_RAW_KEY, sample);
    } catch {
      // ignore
    }
    const parsed = parseStudioRunwayText(sample, channelNameByCode);
    setParseResult(parsed);
    if (!baseDateManual && parsed.baseDateFromText) {
      setBaseDateIso(parsed.baseDateFromText);
    }
  };

  const handleBaseDateChange = (value: string) => {
    const iso = normalizeDateCell(value) || value;
    setBaseDateIso(iso);
    setBaseDateManual(true);
    try {
      localStorage.setItem(STORAGE_BASEDATE_KEY, iso);
      localStorage.setItem(STORAGE_BASEDATE_MANUAL_KEY, "1");
    } catch {
      // ignore
    }
  };

  return (
    <section className="main-content main-content--publishing">
      <div className="publishing-progress__header">
        <div>
          <h2 className="publishing-progress__title">投稿進捗（予約Runway）</h2>
          <p className="publishing-progress__subtitle">{headerText}</p>
        </div>
      </div>

      <div className="publishing-progress__inputPanel">
        <div className="publishing-progress__inputHeader">
          <div className="publishing-progress__inputTitle">貼り付け</div>
          <div className="publishing-progress__inputActions">
            <button type="button" className="workspace-button" onClick={handleParse}>
              解析
            </button>
            <button type="button" className="workspace-button" onClick={handlePasteSample}>
              サンプル
            </button>
            <button type="button" className="workspace-button" onClick={handleClear}>
              クリア
            </button>
          </div>
        </div>

        <textarea
          className="publishing-progress__textarea"
          placeholder="YouTube Studio一覧（抜粋）や、サマリ文をここに貼り付け → 解析"
          value={rawText}
          onChange={(e) => {
            const next = e.target.value;
            setRawText(next);
            try {
              localStorage.setItem(STORAGE_RAW_KEY, next);
            } catch {
              // ignore
            }
          }}
        />

        <div className="publishing-progress__inputFooter">
          <div className="publishing-progress__inputHint">
            解析対象: 表の行（`| ch06 | ... |`）または見出し（`### ch06：...`）を含むテキスト
          </div>
          <div className="publishing-progress__inputRight">
            <label className="publishing-progress__date">
              <span>基準日</span>
              <input type="date" value={baseDateIso} onChange={(e) => handleBaseDateChange(e.target.value)} />
            </label>
            <label className="publishing-progress__limit">
              <span>表示本数</span>
              <select value={upcomingLimit} onChange={(e) => setUpcomingLimit(Number(e.target.value))}>
                <option value={6}>6</option>
                <option value={12}>12</option>
                <option value={24}>24</option>
                <option value={48}>48</option>
              </select>
            </label>
          </div>
        </div>
      </div>

      {parseResult.errors.length ? (
        <div className="main-status">
          {parseResult.errors.map((msg) => (
            <span key={msg} className="status-chip status-chip--danger">
              {msg}
            </span>
          ))}
        </div>
      ) : null}

      {parseResult.warnings.length ? (
        <div className="publishing-progress__warnings">
          <details>
            <summary>警告 {parseResult.warnings.length} 件（クリックで展開）</summary>
            <ul>
              {parseResult.warnings.slice(0, 50).map((w, idx) => (
                <li key={`${idx}-${w}`}>{w}</li>
              ))}
            </ul>
          </details>
        </div>
      ) : null}

      <div className="publishing-progress__controls">
        <div className="publishing-progress__filters" role="group" aria-label="Runway filters">
          <button
            type="button"
            className={`chip ${filter === "all" ? "chip--active" : ""}`}
            onClick={() => setFilter("all")}
          >
            すべて <span className="chip__count">{stats.total}</span>
          </button>
          <button
            type="button"
            className={`chip chip--danger ${filter === "depleted" ? "chip--active" : ""}`}
            onClick={() => setFilter("depleted")}
          >
            枯渇 <span className="chip__count">{stats.depleted}</span>
          </button>
          <button
            type="button"
            className={`chip chip--warn ${filter === "short" ? "chip--active" : ""}`}
            onClick={() => setFilter("short")}
          >
            3日以内 <span className="chip__count">{stats.short}</span>
          </button>
          <button
            type="button"
            className={`chip chip--ok ${filter === "ok" ? "chip--active" : ""}`}
            onClick={() => setFilter("ok")}
          >
            余裕あり <span className="chip__count">{stats.ok}</span>
          </button>
        </div>

        <div className="publishing-progress__search">
          <input
            type="search"
            placeholder="チャンネル検索（CH06 / タイトル）"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="チャンネル検索"
          />
        </div>
      </div>

      <div className="publishing-progress__grid" role="list">
        {visibleChannels.map((ch) => (
          <ChannelCard key={ch.channel} channel={ch} />
        ))}
      </div>

      {parseResult.channels.length > 0 && visibleChannels.length === 0 ? (
        <div className="publishing-progress__empty">
          <span className="status-chip">該当するチャンネルがありません</span>
        </div>
      ) : null}
    </section>
  );
}

function ChannelCard({ channel }: { channel: ChannelRunway }) {
  const tone = runwayTone(channel.scheduleRunwayDays);
  const headerToneClass = `publishing-card publishing-card--${tone}`;
  const lastPublished = formatYmd(channel.lastPublishedDate);
  const lastScheduled = channel.lastScheduledDate ? formatYmd(channel.lastScheduledDate) : "—";
  const upcomingCountText = channel.upcomingCount === null ? "—" : String(channel.upcomingCount);
  const upcomingItems = channel.upcoming ?? [];
  const nextItem = upcomingItems.length > 0 ? upcomingItems[0] : null;

  return (
    <article className={headerToneClass} role="listitem">
      <header className="publishing-card__header">
        <div className="publishing-card__titleRow">
          <div className="publishing-card__title" title={channel.channel}>
            {channel.channelName}
          </div>
          <div className="publishing-card__badges">
            <span className={`badge badge--${tone}`}>{channel.scheduleRunwayDays}d</span>
            <span className="badge badge--muted">{channel.channel}</span>
            {channel.upcomingCount === null ? (
              <span
                className="badge badge--muted"
                title="予約本数/次の予約タイトルは、貼り付け本文に「### chxx：...」の予約リストを含めると表示されます"
              >
                詳細未貼付
              </span>
            ) : null}
          </div>
        </div>

        <div className="publishing-card__meta">
          <div>
            <span className="muted">公開最終</span> {lastPublished}
          </div>
          <div>
            <span className="muted">予約最終</span> {lastScheduled}
          </div>
          <div>
            <span className="muted">予約本数</span> {upcomingCountText}
          </div>
        </div>
      </header>

      <div className="publishing-card__body">
        {nextItem ? (
          <div className="publishing-card__next">
            <div className="publishing-card__nextLabel">次の予約</div>
            <div className="publishing-card__nextValue" title={nextItem.title}>
              <span className="publishing-card__nextTime">{formatYmd(nextItem.date)}</span>
              <span className="publishing-card__nextTitle">{nextItem.title}</span>
            </div>
          </div>
        ) : (
          <div className="publishing-card__next publishing-card__next--empty">
            <div className="publishing-card__nextLabel">次の予約</div>
            <div className="publishing-card__nextValue muted">—</div>
          </div>
        )}

        {upcomingItems.length ? (
          <details className="publishing-card__details">
            <summary>今後の予約を表示（{upcomingItems.length}）</summary>
            <ol className="publishing-card__upcoming">
              {upcomingItems.map((it) => (
                <li key={`${channel.channel}-${it.date}-${it.title}`}>
                  <span className="publishing-card__upcomingTime">{formatYmd(it.date)}</span>
                  <span className="publishing-card__upcomingTitle" title={it.title}>
                    {it.title}
                  </span>
                  {it.label ? <span className="publishing-card__upcomingTag">{it.label}</span> : null}
                </li>
              ))}
            </ol>
          </details>
        ) : null}
      </div>
    </article>
  );
}

import React, { useEffect, useMemo, useState } from "react";
import { fetchUiParams, updateUiParams } from "../api/client";
import { UiParams } from "../api/types";

type FormState = UiParams & { loading: boolean; saving: boolean; error?: string; message?: string };

const defaults: UiParams = {
  image_track_target_count: 44,
  belt_segments: 5,
  belt_text_limit: 20,
  start_offset_sec: 3,
  max_duration_sec: 960,
  allow_extra_video_tracks: true,
};

const BELT_SAMPLE = "帯サンプル: あなたの人生が変わる五つの道標";

export const UiParamsPanel: React.FC = () => {
  const [state, setState] = useState<FormState>({ ...defaults, loading: true, saving: false });
  const [saved, setSaved] = useState<UiParams | null>(null);

  useEffect(() => {
    fetchUiParams()
      .then((res) => {
        setState((s) => ({ ...s, ...res.params, loading: false }));
        setSaved(res.params);
      })
      .catch((e) => setState((s) => ({ ...s, loading: false, error: e.message })));
  }, []);

  const handleChange = (key: keyof UiParams) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.type === "checkbox" ? e.target.checked : Number(e.target.value);
    setState((s) => ({ ...s, [key]: value, message: undefined, error: undefined }));
  };

  const handleSave = async () => {
    setState((s) => ({ ...s, saving: true, error: undefined, message: undefined }));
    try {
      const body: Partial<UiParams> = {
        image_track_target_count: state.image_track_target_count,
        belt_segments: state.belt_segments,
        belt_text_limit: state.belt_text_limit,
        start_offset_sec: state.start_offset_sec,
        max_duration_sec: state.max_duration_sec,
        allow_extra_video_tracks: state.allow_extra_video_tracks,
      };
      const res = await updateUiParams(body);
      setState((s) => ({ ...s, ...res.params, saving: false, message: "保存しました" }));
      setSaved(res.params);
    } catch (e: any) {
      setState((s) => ({ ...s, saving: false, error: e.message || "保存に失敗しました" }));
    }
  };

  const beltPreview = useMemo(() => {
    const limit = state.belt_text_limit || 0;
    const text = BELT_SAMPLE;
    const over = text.length > limit;
    const shown = over ? text.slice(0, limit) + "…" : text;
    return { text, shown, over, limit };
  }, [state.belt_text_limit]);

  if (state.loading) return <div className="card">読み込み中...</div>;

  return (
    <div className="card" style={{ maxWidth: 640 }}>
      <div className="card-header">
        <h3 className="card-title">画像/帯パラメータ</h3>
        <p className="card-subtitle">ドラフト生成時の目安値。UIから簡単に調整できます。</p>
        {saved ? (
          <div className="flex gap-2 flex-wrap text-xs text-gray-600 mt-2">
            <Badge label="適用中" />
            <span>画像本数 {saved.image_track_target_count}</span>
            <span>帯 {saved.belt_segments} 分割</span>
            <span>帯文字 {saved.belt_text_limit} 文字/行</span>
            <span>開始 {saved.start_offset_sec}s</span>
            <span>尺上限 {saved.max_duration_sec}s</span>
          </div>
        ) : null}
      </div>
      <div className="card-body space-y-4">
        <SliderRow
          label="画像本数の目安"
          desc="srt2imagesトラックのセグメント数ターゲット"
          value={state.image_track_target_count}
          onChange={handleChange("image_track_target_count")}
          min={1}
          max={300}
        />
        <SliderRow
          label="帯の分割数"
          desc="帯テキストを何分割で置くかの目安"
          value={state.belt_segments}
          onChange={handleChange("belt_segments")}
          min={1}
          max={20}
        />
        <SliderRow
          label="帯1行の最大文字数目安"
          desc="長すぎる帯テキストを避ける上限"
          value={state.belt_text_limit}
          onChange={handleChange("belt_text_limit")}
          min={5}
          max={80}
        />
        <SliderRow
          label="開始オフセット(秒)"
          desc="画像・帯を置き始める秒数（例: 3秒）"
          value={state.start_offset_sec}
          onChange={handleChange("start_offset_sec")}
          step={0.1}
          min={0}
          max={30}
        />
        <SliderRow
          label="最大尺の目安(秒)"
          desc="srt2imagesトラックの終端目安（例: 960秒）"
          value={state.max_duration_sec}
          onChange={handleChange("max_duration_sec")}
          step={1}
          min={10}
          max={7200}
        />
        <CheckboxRow
          label="手動編集トラックを許容"
          desc="背景・BGMなどsrt2images以外のトラックを許容する（厳密チェックを避ける）"
          checked={state.allow_extra_video_tracks}
          onChange={handleChange("allow_extra_video_tracks")}
        />
        <div className="p-3 rounded bg-gray-50 border text-sm">
          <div className="flex items-center justify-between">
            <span className="font-medium">帯テキストのプレビュー</span>
            <span className={beltPreview.over ? "text-red-600" : "text-gray-600"}>
              {beltPreview.text.length}/{beltPreview.limit} 文字
            </span>
          </div>
          <p className={beltPreview.over ? "text-red-600" : "text-gray-700"}>{beltPreview.shown}</p>
          {beltPreview.over && <p className="text-xs text-red-500">上限を超えています。短く調整してください。</p>}
        </div>

        {state.error && <p className="text-red-600 text-sm">{state.error}</p>}
        {state.message && <p className="text-green-600 text-sm">{state.message}</p>}
        <button className="btn btn-primary" onClick={handleSave} disabled={state.saving}>
          {state.saving ? "保存中..." : "保存"}
        </button>
      </div>
    </div>
  );
};

const Badge: React.FC<{ label: string }> = ({ label }) => (
  <span className="inline-block px-2 py-1 rounded bg-gray-200 text-gray-700">{label}</span>
);

const SliderRow: React.FC<{
  label: string;
  desc?: string;
  value: number;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  min?: number;
  max?: number;
  step?: number;
}> = ({ label, desc, value, onChange, min, max, step = 1 }) => (
  <div className="flex flex-col gap-1">
    <div className="flex items-center justify-between">
      <div>
        <span className="font-medium">{label}</span>
        {desc && <p className="text-xs text-gray-500">{desc}</p>}
      </div>
      <input
        type="number"
        className="input input-bordered w-24 text-right"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={onChange}
      />
    </div>
    <input type="range" min={min} max={max} step={step} value={value} onChange={onChange} />
  </div>
);

const CheckboxRow: React.FC<{
  label: string;
  desc?: string;
  checked: boolean;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
}> = ({ label, desc, checked, onChange }) => (
  <label className="flex items-start gap-2 cursor-pointer">
    <input type="checkbox" className="checkbox mt-1" checked={checked} onChange={onChange} />
    <div>
      <div className="font-medium">{label}</div>
      {desc && <p className="text-xs text-gray-500">{desc}</p>}
    </div>
  </label>
);

export default UiParamsPanel;

import { ThumbnailLibraryGallery } from "./ThumbnailLibraryGallery";

export function ThumbnailHub() {
  return (
    <section className="thumbnail-hub">
      <header className="thumbnail-hub__header">
        <div>
          <h1>サムネイルライブラリ</h1>
          <p className="muted small-text">thumbnails 配下に置いたサムネをチャンネルごとに確認できます</p>
        </div>
      </header>

      <div className="thumbnail-hub__panes">
        <div className="thumbnail-hub__pane thumbnail-hub__pane--primary">
          <ThumbnailLibraryGallery />
        </div>
      </div>
    </section>
  );
}

import { resolveMediaUrl } from "./url";

describe("resolveMediaUrl", () => {
  const BASE = "https://api.example.com/service";

  it("returns null when input is null or empty", () => {
    expect(resolveMediaUrl(null, BASE)).toBeNull();
    expect(resolveMediaUrl(undefined, BASE)).toBeNull();
    expect(resolveMediaUrl("", BASE)).toBeNull();
  });

  it("returns absolute URLs unchanged", () => {
    const absolute = "https://cdn.example.com/audio/file.wav";
    expect(resolveMediaUrl(absolute, BASE)).toBe(absolute);
  });

  it("normalizes protocol-relative URLs", () => {
    expect(resolveMediaUrl("//cdn.example.com/audio/file.wav", BASE, "http:")).toBe(
      "http://cdn.example.com/audio/file.wav"
    );
  });

  it("joins root-relative paths against the base host", () => {
    expect(resolveMediaUrl("/api/channels/CH03/audio", BASE)).toBe("https://api.example.com/api/channels/CH03/audio");
  });

  it("resolves relative paths against the base path segment", () => {
    expect(resolveMediaUrl("media/audio/file.wav", BASE)).toBe("https://api.example.com/service/media/audio/file.wav");
  });
});

import type { ChannelSummary } from "../api/types";
import { calcRaccoFees, generateRaccoListingText, type YoutubeSaleDraft } from "./YoutubeSalePage";

test("calcRaccoFees: 69万円は買主手数料が最低額になる", () => {
  const fees = calcRaccoFees(690_000, "normal");
  expect(fees.buyerFeeYen).toBe(55_000);
  expect(fees.sellerFeeYen).toBe(0);
  expect(fees.buyerPaysYen).toBe(745_000);
  expect(fees.sellerReceivesYen).toBe(690_000);
});

test("generateRaccoListingText: 価格と買主総額が入る", () => {
  const channel: ChannelSummary = {
    code: "CH01",
    video_count: 0,
    youtube_handle: "ch01handle",
    genre: "雑学",
  };

  const draft: YoutubeSaleDraft = {
    listingMode: "normal",
    ownership: "brand",
    operatorDependency: "low",
    salePriceYen: "690000",
    avgMonths: "6",
    subscribers: "35000",
    monthlyViews: "2500000",
    monthlyProfitYen: "120000",
    rightsNote: "",
    customNote: "",
    includePrompts: true,
    includeManual: true,
  };

  const text = generateRaccoListingText({ channel, channelCode: "CH01", draft });
  expect(text).toContain("690,000円（税込）");
  expect(text).toContain("745,000円");
  expect(text).toContain("ブランドアカウント");
  expect(text).toContain("雑学");
});


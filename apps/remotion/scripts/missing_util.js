export function sortMissing(arr = []) {
  return arr.slice().sort((a, b) => (a.idx ?? -1) - (b.idx ?? -1));
}

export function summarizeMissing(arr = []) {
  const summary = { total: arr.length, local: 0, remote: 0 };
  for (const item of arr) {
    if (item?.type === "remote") summary.remote += 1;
    else if (item?.type === "local") summary.local += 1;
  }
  return summary;
}

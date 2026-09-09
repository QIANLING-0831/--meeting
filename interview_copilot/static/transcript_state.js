(function (root) {
  function mergeTranscriptRows(rows, entry) {
    const next = rows.map(row => ({...row}));
    let provisional = -1;
    for (let index = next.length - 1; index >= 0; index -= 1) {
      if (next[index].speaker === entry.speaker && !next[index].final) {
        provisional = index;
        break;
      }
    }
    if (provisional >= 0) {
      next[provisional] = {...entry};
    } else {
      next.push({...entry});
    }
    return next;
  }

  root.mergeTranscriptRows = mergeTranscriptRows;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {mergeTranscriptRows};
  }
})(typeof window !== "undefined" ? window : globalThis);

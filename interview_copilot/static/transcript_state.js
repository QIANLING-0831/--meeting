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

  function composeSelectedQuestion(rows, selectedIndexes) {
    const selected = [...selectedIndexes]
      .sort((left, right) => left - right)
      .map(index => rows[index])
      .filter(entry => entry && entry.speaker === "interviewer" && entry.final)
      .map(entry => entry.text.trim())
      .filter(Boolean);
    let result = "";
    selected.forEach((text, index) => {
      const isLast = index === selected.length - 1;
      let part = isLast ? text : text.replace(/[，,。.!！?？；;：:]+$/u, "");
      if (result.endsWith("有") && part.startsWith("有哪")) part = part.slice(1);
      result += part;
    });
    return result;
  }

  function compactKnowledgeAnswer(text) {
    return String(text || "")
      .split(/\r?\n/u)
      .map(line => line.trim())
      .filter(Boolean)
      .join(" ")
      .replace(/[ \t]+/gu, " ")
      .trim();
  }

  root.mergeTranscriptRows = mergeTranscriptRows;
  root.composeSelectedQuestion = composeSelectedQuestion;
  root.compactKnowledgeAnswer = compactKnowledgeAnswer;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {mergeTranscriptRows, composeSelectedQuestion, compactKnowledgeAnswer};
  }
})(typeof window !== "undefined" ? window : globalThis);

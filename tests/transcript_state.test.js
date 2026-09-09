const assert = require("node:assert/strict");
const {
  mergeTranscriptRows,
  composeSelectedQuestion,
  compactKnowledgeAnswer,
} = require("../interview_copilot/static/transcript_state.js");

let rows = [];
rows = mergeTranscriptRows(rows, {speaker: "interviewer", text: "那", final: false});
rows = mergeTranscriptRows(rows, {speaker: "interviewer", text: "那我们先聊", final: false});
rows = mergeTranscriptRows(rows, {speaker: "interviewer", text: "那我们先聊 Agent", final: false});
assert.equal(rows.length, 1);
assert.equal(rows[0].text, "那我们先聊 Agent");
assert.equal(rows[0].final, false);

rows = mergeTranscriptRows(rows, {speaker: "interviewer", text: "那我们先聊 Agent。", final: true});
assert.equal(rows.length, 1);
assert.equal(rows[0].text, "那我们先聊 Agent。");
assert.equal(rows[0].final, true);

rows = mergeTranscriptRows(rows, {speaker: "candidate", text: "好的", final: true});
assert.equal(rows.length, 2);
assert.equal(rows[1].speaker, "candidate");

const questionRows = [
  {speaker: "interviewer", text: "你说一下 C++ 的特性有", final: true},
  {speaker: "candidate", text: "好的", final: true},
  {speaker: "interviewer", text: "哪些？", final: true},
];
assert.equal(composeSelectedQuestion(questionRows, new Set([0, 2])), "你说一下 C++ 的特性有哪些？");
assert.equal(composeSelectedQuestion(questionRows, new Set([1])), "");

assert.equal(
  compactKnowledgeAnswer("第一点。\n\n- 第二点。\n  第三点。"),
  "第一点。 - 第二点。 第三点。",
);

console.log("transcript state regression: passed");

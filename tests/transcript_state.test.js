const assert = require("node:assert/strict");
const { mergeTranscriptRows } = require("../interview_copilot/static/transcript_state.js");

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

console.log("transcript state regression: passed");

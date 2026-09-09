const $ = id => document.getElementById(id);
let sessionId = null;
let currentQuestion = "";
let lastInterviewerText = "";
let answerBuffer = "";
let transcriptRows = [];
let selectedTranscriptIndexes = new Set();
let restoredSession = null;
let codexModels = [];

function toast(message, error=false){const el=$("toast");el.textContent=message;el.className=error?"show error":"show";setTimeout(()=>el.className="",3600)}
async function api(path, options={}){const response=await fetch(path,options);let data={};try{data=await response.json()}catch{}if(!response.ok)throw new Error(data.detail||`请求失败 ${response.status}`);return data}
function setPill(el, text, ok){el.textContent=text;el.className=`pill ${ok?"ok":"bad"}`}
function applyAnswerSnapshot(snapshot){if(!snapshot?.text)return;if(snapshot.text.length<answerBuffer.length)return;answerBuffer=snapshot.text;currentQuestion=snapshot.question||currentQuestion;$("questionBox").textContent=currentQuestion;$("answerOutput").textContent=answerBuffer;$("answerState").textContent=snapshot.running?"Codex 正在生成…":"已恢复最近回答"}

async function loadStatus(){
  try{const s=await api("/api/status");setPill($("paraformerStatus"),s.paraformerConfigured?"Paraformer 已配置":"缺少 Paraformer Key",s.paraformerConfigured);$("includePoints").checked=Boolean(s.answerSettings?.includeCorePoints);applyAnswerSnapshot(s.answerSnapshot);const micSelect=$("microphoneDevice");micSelect.replaceChildren();(s.microphoneDevices||[]).forEach(device=>{const option=document.createElement("option");option.value=device.id;option.textContent=device.name;option.selected=device.id===(s.selectedMicrophoneDeviceId||s.defaultMicrophoneDeviceId);micSelect.append(option)});if(s.session){restoredSession=s.session;sessionId=s.session.id;$("sessionBadge").textContent=`已恢复 · ${s.session.position||s.session.id}`;$("company").value=s.session.company||"";$("position").value=s.session.position||"";$("jd").value=s.session.jd_text||"";$("facts").value=s.session.candidate_facts||"";$("factsWrap").classList.remove("hidden");$("saveFactsButton").classList.remove("hidden")}}
  catch(e){toast(e.message,true)}
  try{const a=await api("/api/codex/account");const signed=Boolean(a.account);setPill($("codexStatus"),signed?"Codex 已登录":"Codex 未登录",signed);$("loginButton").disabled=signed;$("loginButton").textContent=signed?"已登录":"登录 Codex"}
  catch{setPill($("codexStatus"),"Codex 未连接",false)}
}

async function loadPacks(){
  const data=await api("/api/knowledge/packs");const wrap=$("packs");wrap.replaceChildren();
  if(!data.packs.length){const empty=document.createElement("span");empty.className="muted";empty.textContent="暂无知识库；可先下载 zero2Agent 后重建索引。";wrap.append(empty);return}
  data.packs.forEach((name,i)=>{const label=document.createElement("label");label.className="pack";const input=document.createElement("input");input.type="checkbox";input.value=name;input.checked=restoredSession?restoredSession.knowledge_packs.includes(name):(i===0||name==="agent");label.append(input,document.createTextNode(name));wrap.append(label)})
}
async function loadModels(){const data=await api("/api/codex/models");codexModels=data.models;const select=$("modelSelect");select.replaceChildren();codexModels.forEach(model=>{const option=document.createElement("option");option.value=model.model;option.textContent=model.displayName;option.selected=model.model===data.selectedModel;select.append(option)});renderEfforts(data.selectedEffort);updateModelHint()}
function renderEfforts(preferred="low"){const model=codexModels.find(item=>item.model===$("modelSelect").value);const select=$("effortSelect");select.replaceChildren();(model?.supportedReasoningEfforts||[]).forEach(item=>{const option=document.createElement("option");option.value=item.reasoningEffort;option.textContent=item.reasoningEffort;option.selected=item.reasoningEffort===preferred;select.append(option)})}
function updateModelHint(){const model=$("modelSelect").value;$("modelHint").textContent=model.includes("luna")?"推荐：实时面试速度优先":"更强模型可能回答更细，但首字延迟通常更高"}
async function saveModel(){try{await api("/api/settings/model",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({model:$("modelSelect").value,effort:$("effortSelect").value})});updateModelHint()}catch(e){toast(e.message,true)}}
$("modelSelect").onchange=()=>{renderEfforts("low");saveModel()};$("effortSelect").onchange=saveModel;
$("includePoints").onchange=async()=>{try{await api("/api/settings/answer",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({include_core_points:$("includePoints").checked})});toast($("includePoints").checked?"下一题将生成核心要点和口述版":"下一题将只生成口述回答")}catch(e){toast(e.message,true)}};

$("prepareButton").onclick=async()=>{const form=new FormData();form.append("company",$("company").value);form.append("position",$("position").value);form.append("jd_text",$("jd").value);form.append("knowledge_packs",JSON.stringify([...document.querySelectorAll(".pack input:checked")].map(x=>x.value)));const file=$("resume").files[0];if(file)form.append("resume",file);try{const data=await api("/api/session/prepare",{method:"POST",body:form});sessionId=data.session.id;$("sessionBadge").textContent=`已准备 · ${data.session.position||data.session.id}`;$("facts").value=data.candidateFacts;$("factsWrap").classList.remove("hidden");$("saveFactsButton").classList.remove("hidden");toast("工作区已生成，请核对候选人事实") }catch(e){toast(e.message,true)}};
$("saveFactsButton").onclick=async()=>{try{await api(`/api/session/${encodeURIComponent(sessionId)}/facts`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text:$("facts").value})});toast("候选人事实已确认");collapseSetup(true)}catch(e){toast(e.message,true)}};
$("reindexButton").onclick=async()=>{try{$("reindexButton").disabled=true;const r=await api("/api/knowledge/reindex",{method:"POST"});toast(`索引完成：${r.files} 个文件，${r.chunks} 个片段`);await loadPacks()}catch(e){toast(e.message,true)}finally{$("reindexButton").disabled=false}};
$("microphone").onchange=()=>{$("microphoneDevice").classList.toggle("hidden",!$("microphone").checked);$("candidateLevelWrap").classList.toggle("hidden",!$("microphone").checked)};
$("startButton").onclick=async()=>{try{await api("/api/interview/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({microphone_enabled:$("microphone").checked,microphone_device_id:$("microphoneDevice").value})});setRunning(true)}catch(e){toast(e.message,true)}};
$("stopButton").onclick=async()=>{try{await api("/api/interview/stop",{method:"POST"});setRunning(false)}catch(e){toast(e.message,true)}};
function setRunning(on){$("liveBadge").textContent=on?"识别中":"未开始";$("liveBadge").className=`live-dot ${on?"on":""}`;$("startButton").disabled=on;$("stopButton").disabled=!on;if(!on){["interviewer","candidate"].forEach(prefix=>{$(prefix+"Level").value=0;$(prefix+"LevelText").textContent="未检测"})}}

function addTranscript(entry){transcriptRows=window.mergeTranscriptRows(transcriptRows,entry);renderTranscript();if(entry.speaker==="interviewer"&&entry.final){lastInterviewerText=entry.text;currentQuestion=""}}
function renderTranscript(scrollToBottom=true){const wrap=$("transcript");const previousScroll=wrap.scrollTop;wrap.replaceChildren();transcriptRows.forEach((entry,index)=>{const row=document.createElement("div");const selectable=entry.speaker==="interviewer"&&entry.final;row.className=`utterance ${entry.speaker} ${entry.final?"":"provisional"} ${selectable?"selectable":""} ${selectedTranscriptIndexes.has(index)?"selected":""}`;if(selectable){row.title="点击选择或取消这个问题片段";row.onclick=()=>{selectedTranscriptIndexes.has(index)?selectedTranscriptIndexes.delete(index):selectedTranscriptIndexes.add(index);renderTranscript(false);updateSelectionControls()}}const who=document.createElement("span");who.className="speaker";who.textContent=entry.speaker==="interviewer"?"面试官 / 系统声音":"我 / 麦克风";const text=document.createElement("span");text.textContent=entry.text;row.append(who,text);wrap.append(row)});wrap.scrollTop=scrollToBottom?wrap.scrollHeight:previousScroll}
function updateSelectionControls(){const count=selectedTranscriptIndexes.size;$("selectionHint").textContent=count?`已选择 ${count} 个问题片段`:`可点击面试官转写，手动选择问题片段`;$("setQuestionButton").disabled=!count;$("clearSelectionButton").disabled=!count}
$("clearSelectionButton").onclick=()=>{selectedTranscriptIndexes.clear();renderTranscript(false);updateSelectionControls()};
$("setQuestionButton").onclick=()=>{const selected=window.composeSelectedQuestion(transcriptRows,selectedTranscriptIndexes);if(!selected)return toast("请选择完整的面试官转写",true);currentQuestion=selected;lastInterviewerText=selected;$("questionBox").textContent=selected;$("answerState").textContent="已手动选择";selectedTranscriptIndexes.clear();renderTranscript(false);updateSelectionControls();toast("已设为当前问题，按 F8 即可回答")};
$("sendTextButton").onclick=async()=>{const text=$("manualText").value.trim();if(!text)return;try{await api("/api/transcript/simulate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({speaker:$("manualSpeaker").value,text,final:true})});$("manualText").value=""}catch(e){toast(e.message,true)}};
$("manualText").addEventListener("keydown",e=>{if(e.key==="Enter")$("sendTextButton").click()});
$("answerButton").onclick=()=>requestAnswer(currentQuestion||lastInterviewerText);async function requestAnswer(q){if(!q)return toast("还没有可回答的问题",true);try{await api("/api/questions/answer",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({question:q})})}catch(e){toast(e.message,true)}}
$("interruptButton").onclick=async()=>{try{await api("/api/questions/interrupt",{method:"POST"});$("answerState").textContent="已打断"}catch(e){toast(e.message,true)}};
$("loginButton").onclick=async()=>{try{$("loginButton").disabled=true;const data=await api("/api/codex/login",{method:"POST"});if(data.type==="alreadyLoggedIn"){setPill($("codexStatus"),"Codex 已登录",true);$("loginButton").textContent="已登录";return toast("当前 ChatGPT Plus 已登录，无需重复授权")}const url=data.authUrl||data.auth_url||data.url;if(url)window.open(url,"_blank","noopener");toast("请在新页面完成 ChatGPT 授权，完成后返回这里");$("loginButton").disabled=false}catch(e){$("loginButton").disabled=false;toast(e.message,true)}};

function connect(){const ws=new WebSocket(`${location.protocol==="https:"?"wss":"ws"}://${location.host}/ws`);ws.onopen=async()=>{try{const status=await api("/api/status");applyAnswerSnapshot(status.answerSnapshot)}catch{}};ws.onmessage=e=>handleEvent(JSON.parse(e.data));ws.onclose=()=>{$("answerState").textContent="页面连接恢复中…";setTimeout(connect,1500)}}
function handleEvent(event){
  if(event.type==="transcript")addTranscript(event.entry);
  if(event.type==="question_candidate"){currentQuestion=event.question.text;$("questionBox").textContent=currentQuestion;$("answerState").textContent=`已识别 · ${event.question.question_type}`}
  if(event.type==="knowledge_hits"){currentQuestion=event.question;$("questionBox").textContent=currentQuestion;const wrap=$("knowledgeHits");wrap.replaceChildren();event.hits.slice(0,1).forEach(h=>{const card=document.createElement("div");card.className="hit";const badge=document.createElement("b");badge.textContent=`${h.pack} · 题库速答`;const matchedQuestion=document.createElement("div");matchedQuestion.className="hit-question";matchedQuestion.textContent=`匹配原题：${String(h.title||"未命名条目").replace(/^Q[：:]\s*/u,"")}`;const snippet=document.createElement("span");snippet.textContent=window.compactKnowledgeAnswer(h.answer_preview||h.content).slice(0,620);card.append(badge,matchedQuestion,snippet);wrap.append(card)})}
  if(event.type==="answer_started"){answerBuffer="";$("answerOutput").textContent="";$("answerState").textContent="Codex 正在生成…"}
  if(event.type==="answer_retrying")$("answerState").textContent=event.message||"Codex 响应较慢，继续等待…";
  if(event.type==="answer_delta"){answerBuffer+=event.delta;$("answerOutput").textContent=answerBuffer;$("answerOutput").scrollTop=$("answerOutput").scrollHeight}
  if(event.type==="answer_completed")$("answerState").textContent=event.status==="completed"?"回答完成":"生成已结束";
  if(event.type==="audio_level"){const prefix=event.speaker==="interviewer"?"interviewer":"candidate";const level=Math.max(0,Math.min(1,Number(event.level)||0));$(prefix+"Level").value=level;$(prefix+"LevelText").textContent=level<.18?"偏小":level>.82?"过大":"正常"}
  if(event.type==="interview_state")setRunning(event.running);
  if(event.type==="notice")toast(event.message);
  if(event.type==="error"){$("answerState").textContent="连接失败 · 可按 F8 重试";toast(event.message,true)}
}
document.addEventListener("keydown",e=>{if(e.key==="F8"){e.preventDefault();requestAnswer(currentQuestion||lastInterviewerText)}if(e.key==="Escape")$("interruptButton").click()});
function collapseSetup(collapsed){$("setupPanel").classList.toggle("collapsed",collapsed);document.querySelector("main").classList.toggle("setup-collapsed",collapsed);$("setupToggle").textContent=collapsed?"展开":"收起";$("setupToggle").setAttribute("aria-expanded",String(!collapsed))}
$("setupToggle").onclick=()=>collapseSetup(!$("setupPanel").classList.contains("collapsed"));
async function initialize(){await loadStatus();await Promise.all([loadPacks(),loadModels()]).catch(e=>toast(e.message,true));connect()}
initialize();

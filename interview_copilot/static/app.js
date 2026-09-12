const $ = id => document.getElementById(id);
let sessionId = null;
let answerBuffer = "";
let responseId = "";
let keyMasked = "";

function toast(message, error=false){const el=$("toast");el.textContent=message;el.className=error?"show error":"show";setTimeout(()=>el.className="",3600)}
async function api(path, options={}){const response=await fetch(path,options);let data={};try{data=await response.json()}catch{}if(!response.ok)throw new Error(data.detail||`请求失败 ${response.status}`);return data}
function setPill(text, ok){const el=$("aliyunStatus");el.textContent=text;el.className=`pill ${ok?"ok":"bad"}`}
function setRunning(on){$("liveBadge").textContent=on?"Qwen 监听中":"未开始";$("liveBadge").className=`live-dot ${on?"on":""}`;$("startButton").disabled=on;$("stopButton").disabled=!on;["loopbackDevice","realtimeModel","turnDetection","workspaceId","saveRealtimeButton"].forEach(id=>$(id).disabled=on);if(!on){$("interviewerLevel").value=0;$("interviewerLevelText").textContent="未检测"}}

async function loadStatus(){
  try{
    const s=await api("/api/status");keyMasked=s.aliyunKeyMasked||"";setPill(s.aliyunConfigured?`阿里云已配置${keyMasked?` · ${keyMasked}`:""}`:"缺少阿里云 Key",s.aliyunConfigured);
    const settings=s.realtimeSettings||{};$("realtimeModel").value=settings.model||"qwen-audio-3.0-realtime-plus";$("turnDetection").value=settings.turnDetection||"smart_turn";$("workspaceId").value=settings.workspaceId||"";
    const devices=$("loopbackDevice");devices.replaceChildren();(s.loopbackDevices||[]).forEach((device,index)=>{const option=document.createElement("option");option.value=device.name;option.textContent=device.name;option.selected=device.name===s.selectedLoopbackDeviceName||(!s.selectedLoopbackDeviceName&&index===0);devices.append(option)});
    if(s.session){sessionId=s.session.id;$("sessionBadge").textContent=`已恢复 · ${s.session.position||s.session.id}`;$("company").value=s.session.company||"";$("position").value=s.session.position||"";$("jd").value=s.session.jd_text||"";$("facts").value=s.session.candidate_facts||"";$("factsWrap").classList.remove("hidden");$("saveFactsButton").classList.remove("hidden")}
    const snapshot=s.answerSnapshot||{};if(snapshot.question)$("questionBox").textContent=snapshot.question;if(snapshot.text){answerBuffer=snapshot.text;$("answerOutput").textContent=answerBuffer}setRunning(Boolean(s.running));
  }catch(e){toast(e.message,true)}
}

$("prepareButton").onclick=async()=>{const form=new FormData();form.append("company",$("company").value);form.append("position",$("position").value);form.append("jd_text",$("jd").value);const file=$("resume").files[0];if(file)form.append("resume",file);try{const data=await api("/api/session/prepare",{method:"POST",body:form});sessionId=data.session.id;$("sessionBadge").textContent=`已准备 · ${data.session.position||data.session.id}`;$("facts").value=data.candidateFacts;$("factsWrap").classList.remove("hidden");$("saveFactsButton").classList.remove("hidden");toast("资料已载入，请核对候选人事实") }catch(e){toast(e.message,true)}};
$("saveFactsButton").onclick=async()=>{try{await api(`/api/session/${encodeURIComponent(sessionId)}/facts`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text:$("facts").value})});toast("候选人事实已保存；下次启动 Qwen 时生效")}catch(e){toast(e.message,true)}};
$("saveRealtimeButton").onclick=async()=>{try{await api("/api/settings/realtime",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({model:$("realtimeModel").value,workspace_id:$("workspaceId").value.trim(),turn_detection:$("turnDetection").value})});toast("Qwen 设置已保存")}catch(e){toast(e.message,true)}};
$("startButton").onclick=async()=>{try{await api("/api/interview/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({loopback_device_name:$("loopbackDevice").value})});setRunning(true)}catch(e){toast(e.message,true)}};
$("stopButton").onclick=async()=>{try{await api("/api/interview/stop",{method:"POST"});setRunning(false)}catch(e){toast(e.message,true)}};

$("aliyunKeyButton").onclick=()=>{$("aliyunKey").value="";$("aliyunKeyConfirm").value="";$("aliyunKeyHint").textContent=keyMasked?`当前 Key：${keyMasked}。请输入两遍新 Key。`:"请输入两遍 API Key；完整内容不会在页面回显。";$("aliyunKeyDialog").showModal()};
$("aliyunKeyCancel").onclick=()=>$("aliyunKeyDialog").close();
$("aliyunKeyForm").onsubmit=async event=>{event.preventDefault();const key=$("aliyunKey").value.trim();const confirmation=$("aliyunKeyConfirm").value.trim();if(key!==confirmation)return toast("两次输入的 API Key 不一致",true);try{const saved=await api("/api/settings/aliyun-key",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({api_key:key,confirm_api_key:confirmation})});keyMasked=saved.masked;setPill(`阿里云已配置 · ${saved.masked}`,true);$("aliyunKeyDialog").close();toast("阿里云 API Key 已保存")}catch(e){toast(e.message,true)}};

function addTranscript(entry){const wrap=$("transcript");if(wrap.querySelector(".empty"))wrap.replaceChildren();const row=document.createElement("div");row.className="utterance interviewer";const who=document.createElement("span");who.className="speaker";who.textContent="面试官 / Qwen";const text=document.createElement("span");text.textContent=entry.text;row.append(who,text);wrap.append(row);wrap.scrollTop=wrap.scrollHeight}
function handleEvent(event){
  if(event.type==="transcript")addTranscript(event.entry);
  if(event.type==="fast_channel_ready")$("answerState").textContent="已连接 · 等待问题";
  if(event.type==="fast_question_transcript"&&event.text){$("questionBox").textContent=event.text;$("answerState").textContent="已识别问题"}
  if(event.type==="fast_answer_started"){responseId=event.responseId||"";answerBuffer="";$("answerOutput").textContent="";$("answerState").textContent="Qwen 正在生成…"}
  if(event.type==="fast_answer_delta"&&(!responseId||event.responseId===responseId)){answerBuffer+=event.delta||"";$("answerOutput").textContent=answerBuffer;$("answerOutput").scrollTop=$("answerOutput").scrollHeight}
  if(event.type==="fast_answer_text_done"&&(!responseId||event.responseId===responseId)&&event.text){answerBuffer=event.text;$("answerOutput").textContent=answerBuffer}
  if(event.type==="fast_answer_completed"&&(!responseId||event.responseId===responseId))$("answerState").textContent=event.status==="completed"?"回答完成":"回答已结束";
  if(event.type==="fast_answer_error"){$("answerState").textContent="Qwen 通道异常";toast(event.message,true)}
  if(event.type==="audio_level"&&event.speaker==="interviewer"){$("interviewerLevel").value=event.level;$("interviewerLevelText").textContent=event.level>0.04?"有声音":"静音"}
  if(event.type==="audio_status"&&event.speaker==="interviewer")$("interviewerLevelText").textContent=event.message;
  if(event.type==="interview_state")setRunning(event.running);
}
function connect(){const ws=new WebSocket(`${location.protocol==="https:"?"wss":"ws"}://${location.host}/ws`);ws.onmessage=e=>handleEvent(JSON.parse(e.data));ws.onclose=()=>{if(!$("stopButton").disabled)$("answerState").textContent="页面连接恢复中…";setTimeout(connect,1500)}}

loadStatus();connect();

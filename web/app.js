"use strict";
const $ = id => document.getElementById(id);
const {splitForSpeech, ordered, toPlainText} = globalThis.TingjianText;
let config = null, state = "loading", mic = null, runNumber = 0, followLatest = true;
const records = new Map();
let uploadAbort = null, speechAbort = null, speaking = false, cancelPlayback = null, currentAudioURL = null;

function notice(message = "") { $("notice").textContent = message; $("notice").hidden = !message; }
function setState(next, label) {
  state = next;
  const busy = ["starting", "listening", "finishing", "uploading"].includes(next);
  $("statusText").textContent = label || ({idle:"准备好了",starting:"正在连接麦克风",listening:"正在听，请说话",finishing:"正在整理最后一句",uploading:"正在处理录音",loading:"正在连接",error:"连接未成功"}[next] || next);
  $("status").className = `status ${next}`;
  $("recordLabel").textContent = ({idle:"开始听写",starting:"取消连接",listening:"停止听写",finishing:"正在整理",uploading:"取消上传",loading:"正在连接",error:"重新连接"}[next] || "开始听写");
  $("record").disabled = next === "loading" || next === "finishing" || (!config && next !== "error");
  $("record").classList.toggle("listening", next === "listening");
  $("read").disabled = !config?.tts || busy || speaking;
  $("upload").disabled = !config || busy || speaking;
  $("clear").disabled = busy || speaking;
  $("logout").disabled = busy || speaking;
  $("level").hidden = next !== "listening";
}
async function api(path, options = {}) {
  const headers = {...(options.headers || {})};
  if (config?.csrf) headers["X-CSRF-Token"] = config.csrf;
  const response = await fetch(path, {...options, headers, credentials: "same-origin", cache: "no-store"});
  if (!response.ok) {
    let message = `请求未成功（${response.status}）`;
    try { message = (await response.json()).detail || message; } catch (_) { /* preserve status */ }
    if (response.status === 401 && path !== "/api/login") {
      config = null;
      if (!$("loginDialog").open) $("loginDialog").showModal();
    }
    throw new Error(message);
  }
  return response;
}
async function refreshSession() {
  const response = await fetch("/api/session", {credentials:"same-origin",cache:"no-store"});
  if (response.status === 401) {
    config = null; setState("idle", "请先输入访问码");
    if (!$("loginDialog").open) $("loginDialog").showModal();
    return false;
  }
  if (!response.ok) throw new Error("服务器暂时无法连接，请稍后点击“重新连接”");
  config = await response.json();
  $("uploadHint").textContent = `支持 WAV、MP3、M4A、WebM 等录音。最长 ${Math.floor(config.max_upload_seconds/60)} 分钟，最大 ${config.max_upload_mb} MB。`;
  $("readHint").textContent = config.tts ? "先停止听写，再点朗读，避免声音被重复识别。" : "此服务器未开启普通话朗读，听写功能可正常使用。";
  setState("idle"); return true;
}
function scrollLatest() {
  if (followLatest) $("captions").scrollTop = $("captions").scrollHeight;
  $("follow").hidden = followLatest;
}
function addFinal(run, message) {
  if (!Number.isInteger(message.id)) return;
  const text = String(message.text || "").trim();
  if (text) {
    const key = `${run}:${message.id}`;
    let record = records.get(key);
    if (!record) { record = {run, id:message.id, text, element:document.createElement("p")}; records.set(key, record); }
    record.text = text; record.element.textContent = text;
    const sorted = ordered(records);
    const next = sorted[sorted.indexOf(record)+1]?.element;
    if (record.element.parentNode !== $("finals")) $("finals").insertBefore(record.element, next?.parentNode ? next : null);
    $("empty").hidden = true;
    $("counter").textContent = `已确认 ${records.size} 句`;
  }
  if ($("partialBox").dataset.run === String(run) && Number($("partialBox").dataset.id) <= message.id) {
    $("partialBox").hidden = true; $("partial").textContent = "";
  }
  scrollLatest();
}
function showPartial(run, message) {
  if (records.has(`${run}:${message.id}`)) return;
  $("empty").hidden = true;
  $("partialBox").hidden = false;
  $("partialBox").dataset.run = String(run); $("partialBox").dataset.id = String(message.id);
  document.querySelector(".partial-label").textContent = "正在听 · 这句话还可能修改";
  $("partial").textContent = String(message.text || ""); scrollLatest();
}
function uncertainPartial() {
  if (!$("partialBox").hidden) document.querySelector(".partial-label").textContent = "这句话未确认 · 请重新说一遍";
}

class MicrophoneSession {
  constructor(run) {
    this.run = run; this.stopped = false; this.stopping = false; this.acceptAudio = false;
    this.context = null; this.stream = null; this.node = null; this.socket = null; this.lock = null;
    this.finished = false; this.initialCount = records.size; this.lastVoice = Date.now(); this.lastMeter = 0;
  }
  ensureActive() { if (this.stopped || mic !== this) throw new DOMException("已取消", "AbortError"); }
  async start() {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error("麦克风需要安全连接，请使用 HTTPS 地址，并在 Safari 或 Chrome 中打开");
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) throw new Error("此浏览器不能收音，请使用较新的 Safari 或 Chrome");
    this.context = new Context({latencyHint:"interactive"});
    await this.context.resume(); this.ensureActive();
    this.stream = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
    if (this.stopped) { this.stream.getTracks().forEach(t=>t.stop()); this.ensureActive(); }
    if (!this.context.audioWorklet) throw new Error("浏览器缺少音频采集能力，请更新 Safari 或 Chrome");
    await this.context.audioWorklet.addModule("/assets/pcm-worklet.js"); this.ensureActive();
    this.node = new AudioWorkletNode(this.context, "pcm-capture", {numberOfInputs:1,numberOfOutputs:1,outputChannelCount:[1]});
    this.node.port.onmessage = event => {
      const data = event.data;
      if (data.type === "flushed") { this.flushed?.(); return; }
      if (data.type !== "audio" || !this.acceptAudio) return;
      if (this.socket?.readyState !== WebSocket.OPEN || this.socket.bufferedAmount > 256 * 1024) {
        this.fail("网络跟不上收音，已经暂停。已确认的文字还在，请网络恢复后重试。"); return;
      }
      this.socket.send(data.buffer);
      const now = Date.now();
      if (data.rms > 0.012) this.lastVoice = now;
      if (now-this.lastMeter > 120) { $("level").value = Math.min(1, data.rms*8); this.lastMeter = now; }
      if (now-this.lastVoice > 12000) $("hint").textContent = "声音较小，请让说话的人靠近麦克风。";
      else $("hint").textContent = config.partial ? "正在听。停顿后会确认这句话。" : "正在听，停顿后显示完整句子。";
    };
    this.stream.getTracks().forEach(track => { track.onended = () => { if (!this.stopped && !this.stopping) this.fail("麦克风连接中断，请重新开始听写。"); }; });
    const url = new URL("/api/live", location.href); url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
    this.socket = new WebSocket(url);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(()=>reject(new Error("连接服务器超时，请稍后再试")),20000);
      this.readyReject = reject;
      this.socket.onopen = () => this.socket.send(JSON.stringify({type:"start",csrf:config.csrf,sample_rate:this.context.sampleRate}));
      this.socket.onerror = () => { clearTimeout(timer); reject(new Error("无法建立听写连接，请检查网络")); };
      this.socket.onmessage = event => {
        let message; try { message = JSON.parse(event.data); } catch (_) { this.fail("服务器返回了异常消息，请重试"); return; }
        if (message.type === "ready") { clearTimeout(timer); resolve(); }
        else if (message.type === "partial") showPartial(this.run, message);
        else if (message.type === "final") addFinal(this.run, message);
        else if (message.type === "notice") notice(message.message);
        else if (message.type === "error") { clearTimeout(timer); reject(new Error(message.message)); this.fail(message.message); }
        else if (message.type === "done") {
          this.finished = true;
          if (message.ok) { $("partialBox").hidden = true; if (records.size === this.initialCount) notice("这次没有识别到清楚的语音，请靠近麦克风再试。"); }
          else uncertainPartial();
          this.cleanup();
          if (mic === this) { mic = null; setState("idle", "已停止听写"); $("hint").textContent = "已确认的文字已保留，可朗读或保存。"; }
        }
      };
      this.socket.onclose = () => {
        clearTimeout(timer); reject(new Error("听写连接已关闭"));
        if (!this.stopped && !this.finished) this.fail("网络连接中断，已确认的文字已保留。最后一句请重新说一遍。");
      };
    });
    this.ensureActive(); this.acceptAudio = true;
    this.source = this.context.createMediaStreamSource(this.stream); this.source.connect(this.node); this.node.connect(this.context.destination);
    setState("listening");
    if (navigator.wakeLock) navigator.wakeLock.request("screen").then(lock => { if (this.stopped) lock.release(); else this.lock = lock; }).catch(()=>{});
  }
  async stop() {
    if (this.stopped || this.stopping) return;
    this.stopping = true;
    if (state === "starting") { this.readyReject?.(new DOMException("已取消", "AbortError")); this.cleanup(); if (mic === this) {mic=null;setState("idle");} return; }
    setState("finishing");
    let flushed = false;
    if (this.node) await new Promise(resolve => {
      const timer = setTimeout(resolve, 800);
      this.flushed = () => { flushed = true; clearTimeout(timer); resolve(); };
      this.node.port.postMessage("flush");
    });
    this.acceptAudio = false;
    this.stream?.getTracks().forEach(t=>t.stop());
    if (!flushed) notice("末尾一小段声音可能未送达，请核对最后一句。");
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify({type:"stop"}));
    else { this.fail("连接已经中断，请重新开始。"); return; }
    this.stopTimer = setTimeout(()=>this.fail("等待定稿超时，已确认文字已保留，请重试。"),200000);
  }
  fail(message) {
    if (this.stopped) return;
    uncertainPartial(); this.cleanup();
    if (mic === this) { mic = null; notice(message); setState("idle", "已暂停，请重试"); }
  }
  cleanup() {
    this.stopped = true; this.acceptAudio = false; clearTimeout(this.stopTimer);
    this.stream?.getTracks().forEach(t=>t.stop());
    try { this.source?.disconnect(); this.node?.disconnect(); } catch (_) {}
    this.context?.close().catch(()=>{}); this.lock?.release().catch(()=>{});
    if (this.socket && this.socket.readyState < 2) this.socket.close();
  }
}

async function startListening() {
  if (!config) { await refreshSession(); return; }
  stopSpeech(); notice(); $("partialBox").hidden = true; followLatest = true;
  const session = new MicrophoneSession(++runNumber); mic = session; setState("starting");
  try { await session.start(); }
  catch (error) {
    const message = ({NotAllowedError:"没有获得麦克风权限。请点地址栏旁的权限按钮，允许本站使用麦克风后再试。",NotFoundError:"没有找到麦克风，请连接麦克风后再试。",NotReadableError:"麦克风正被其他应用占用，请关闭相关应用后重试。"}[error.name] || error.message);
    if (error.name !== "AbortError" && !session.stopped) session.fail(message);
    session.cleanup(); if (mic === session) {mic=null;setState("idle");}
  }
}
$("record").addEventListener("click", async () => {
  if (state === "uploading") { uploadAbort?.abort(); return; }
  if (mic) { await mic.stop(); return; }
  if (state === "error") { try { await refreshSession(); notice(); } catch (e) {notice(e.message);} return; }
  await startListening();
});
$("loginForm").addEventListener("submit", async event => {
  event.preventDefault(); $("loginSubmit").disabled = true; $("loginError").textContent = "正在验证……";
  try {
    await api("/api/login", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code:$("code").value})});
    $("code").value = ""; await refreshSession(); $("loginDialog").close(); notice(); $("record").focus();
  } catch (e) {$("loginError").textContent = e.message;}
  finally {$("loginSubmit").disabled = false;}
});
$("loginDialog").addEventListener("cancel", e=>e.preventDefault());
$("captions").addEventListener("scroll", () => {
  const el = $("captions"); followLatest = el.scrollHeight-el.scrollTop-el.clientHeight < 65;
  $("follow").hidden = followLatest;
}, {passive:true});
$("follow").addEventListener("click", () => {followLatest=true;scrollLatest();});
for (const button of document.querySelectorAll(".font-option")) button.addEventListener("click", () => {
  document.body.dataset.font = button.dataset.size;
  for (const b of document.querySelectorAll(".font-option")) {b.classList.toggle("selected",b===button);b.setAttribute("aria-pressed",String(b===button));}
  try {localStorage.setItem("tingjian-font",button.dataset.size);} catch (_) {}
  scrollLatest();
});
try { const saved = localStorage.getItem("tingjian-font"); if (["large","larger","largest"].includes(saved)) document.querySelector(`[data-size="${saved}"]`).click(); } catch (_) {}
$("save").addEventListener("click", () => {
  const text = toPlainText(records); if (!text) {notice("还没有可以保存的已确认文字。");return;}
  const url = URL.createObjectURL(new Blob(["\ufeff"+text+"\n"],{type:"text/plain;charset=utf-8"}));
  const a = document.createElement("a"); a.href=url; a.download=`听见-${new Date().toISOString().slice(0,10)}.txt`;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);
});
$("clear").addEventListener("click",()=>$("confirmDialog").showModal());
$("clearCancel").addEventListener("click",()=>$("confirmDialog").close());
$("clearConfirm").addEventListener("click",()=>{
  records.clear();$("finals").replaceChildren();$("partialBox").hidden=true;$("empty").hidden=false;$("counter").textContent="山东话 · 中文转写";notice();$("confirmDialog").close();
});
$("logout").addEventListener("click",async()=>{
  try {await api("/api/logout",{method:"POST"});config=null;records.clear();$("finals").replaceChildren();$("partialBox").hidden=true;$("partial").textContent="";$("speakText").value="";$("counter").textContent="山东话 · 中文转写";$("empty").hidden=false;await refreshSession();}catch(e){notice(e.message);}
});
$("upload").addEventListener("click",()=>$("file").click());
$("file").addEventListener("change",async()=>{
  const file=$("file").files[0];$("file").value="";if(!file||!config)return;
  if(file.size>config.max_upload_mb*1024*1024){notice(`录音太大了，请使用 ${config.max_upload_mb} MB 以内的文件。`);return;}
  const run=++runNumber;let done=false,hadError=false;uploadAbort=new AbortController();setState("uploading","正在上传录音");notice();followLatest=true;
  try {
    const response=await api("/api/transcribe",{method:"POST",headers:{"Content-Type":"application/octet-stream"},body:file,signal:uploadAbort.signal});
    if(!response.body)throw new Error("浏览器无法读取识别结果，请更新浏览器后重试");
    const reader=response.body.getReader(), decoder=new TextDecoder();let buffer="";
    const processLine=line=>{
      if(!line.trim())return; const message=JSON.parse(line);
      if(message.type==="final")addFinal(run,message);
      if(message.progress!==undefined)$("statusText").textContent=`正在识别 ${message.progress}%`;
      if(message.type==="error"){hadError=true;notice(message.message);}
      if(message.type==="done"){done=message.ok;if(!message.segments)notice("这份录音中没有找到清楚的语音，请换一份录音试试。");}
    };
    while(true){const {value,done:ended}=await reader.read();if(ended)break;buffer+=decoder.decode(value,{stream:true});let index;while((index=buffer.indexOf("\n"))>=0){processLine(buffer.slice(0,index));buffer=buffer.slice(index+1);}}
    buffer+=decoder.decode();if(buffer.trim())processLine(buffer);
    if(!done&&!hadError)throw new Error("录音未全部识别完成，已有文字已保留，请重试");
  }catch(e){notice(e.name==="AbortError"?"已取消上传，已经识别出来的文字仍然保留。":e.message);}
  finally{uploadAbort=null;setState("idle",done?"录音识别完成":"已停止处理");}
});

function stopSpeech() {
  speechAbort?.abort();speechAbort=null;cancelPlayback?.();cancelPlayback=null;
  $("audio").pause();$("audio").removeAttribute("src");$("audio").load();$("audio").hidden=true;
  if(currentAudioURL){URL.revokeObjectURL(currentAudioURL);currentAudioURL=null;}
  speaking=false;$("speak").textContent="开始朗读";$("speakText").disabled=false;$("speed").disabled=false;setState(state);
}
function playBlob(blob, signal) {
  if(currentAudioURL)URL.revokeObjectURL(currentAudioURL);
  currentAudioURL=URL.createObjectURL(blob);const audio=$("audio");audio.src=currentAudioURL;audio.hidden=false;
  return new Promise((resolve,reject)=>{
    let settled=false;
    const end=(error)=>{if(settled)return;settled=true;audio.onended=null;audio.onerror=null;signal.removeEventListener("abort",abort);cancelPlayback=null;error?reject(error):resolve();};
    const abort=()=>end(new DOMException("已停止", "AbortError"));signal.addEventListener("abort",abort,{once:true});cancelPlayback=abort;
    audio.onended=()=>end();audio.onerror=()=>end(new Error("播放没有成功，请重试"));
    audio.play().catch(()=>{$("speechStatus").textContent="浏览器需要您点一下下方的播放键。";});
  });
}
$("read").addEventListener("click",()=>{
  const text=toPlainText(records), chars=Array.from(text);$("speakText").value=chars.slice(-1500).join("");
  $("speechStatus").textContent=chars.length>1500?"已带入最近 1500 字，您可以修改后朗读。":"先确认文字，再开始朗读。";
  $("readDialog").showModal();
});
$("readClose").addEventListener("click",()=>{stopSpeech();$("readDialog").close();});
$("readDialog").addEventListener("cancel",()=>stopSpeech());
$("speak").addEventListener("click",async()=>{
  if(speaking){stopSpeech();$("speechStatus").textContent="已停止朗读。";return;}
  const chunks=splitForSpeech($("speakText").value);if(!chunks.length){$("speechStatus").textContent="请先写一些想朗读的文字。";return;}
  speaking=true;speechAbort=new AbortController();const controller=speechAbort;$("speak").textContent="停止朗读";$("speakText").disabled=true;$("speed").disabled=true;setState(state);
  const prepare=text=>api("/api/tts",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text,speed:Number($("speed").value)}),signal:controller.signal}).then(r=>r.blob()).then(blob=>({blob})).catch(error=>({error}));
  let next=prepare(chunks[0]);
  try{
    for(let i=0;i<chunks.length;i++){
      $("speechStatus").textContent=`正在准备第 ${i+1} / ${chunks.length} 段……`;
      const result=await next;if(result.error)throw result.error;if(controller.signal.aborted)throw new DOMException("已停止","AbortError");
      // At most one segment of prefetch: lower latency without unlimited TTS work.
      if(i+1<chunks.length)next=prepare(chunks[i+1]);
      $("speechStatus").textContent=`正在朗读第 ${i+1} / ${chunks.length} 段`;
      await playBlob(result.blob,controller.signal);
    }
    $("speechStatus").textContent="朗读完成。";
  }catch(e){$("speechStatus").textContent=e.name==="AbortError"?"已停止朗读。":e.message;}
  finally{if(speechAbort===controller)stopSpeech();}
});
document.addEventListener("visibilitychange",()=>{if(document.hidden){mic?.stop();stopSpeech();}});
window.addEventListener("beforeunload",event=>{if(mic||uploadAbort||records.size){event.preventDefault();event.returnValue="";}});
window.addEventListener("pagehide",()=>{mic?.cleanup();uploadAbort?.abort();stopSpeech();});
refreshSession().catch(e=>{notice(e.message);setState("error");});

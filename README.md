# Qwen Realtime Interview Copilot

这个分支默认使用 Qwen-Audio Realtime Plus 单模型链路。运行链路不启动 Paraformer，也不调用 Codex；页面同时保留“专用 Qwen 流式 ASR → Qwen Plus”的实验模式。

> 使用前请取得面试参与者同意，并遵守当地法律、公司政策和面试规则。程序不保存原始音频，只在本机保存文字记录。

## 数据链路

```text
会议系统声音 → Qwen-Audio Realtime Plus → 浏览器
```

Qwen 返回的输入转写直接显示在左侧，因此不会再出现 Paraformer 与 Qwen 两套独立话轮进度不一致的问题。实验双模型模式会把简历、JD 和项目描述中的技术词作为识别上下文与热词发送。浏览器 WebSocket 会一次排空已经到达的事件，避免按固定速率逐条发送造成积压。

## 面试资料

准备面试时可以上传 PDF、Markdown 或 TXT 简历，填写 JD，并在“候选人事实与项目描述”中核对或补充项目经历。工作台还会列出 `workspace/knowledge/packs` 中的本地知识库；勾选后，对每道题从本地 SQLite FTS 索引召回最多 6 个相关片段：

- 检索完全在本机完成，不访问网页，也不产生第二次模型调用。
- 不会把整套知识库塞入提示词，避免长上下文拖慢回答。
- 未勾选的知识库不会进入回答上下文。

所有资料由 `AnswerContextProvider` 统一组装。新增回答模型时应作为模型适配器接在 `QwenOnlyEngine` 的回答入口之后；模型会自动获得同一份 JD、候选人事实、外部资料、本地检索结果与追问历史，不应自行重复读取这些文件。

## 安装与启动

需要 Windows 10/11、Python 3.11+ 和阿里云百炼 API Key。

```powershell
git clone -b codex/qwen-only-realtime https://github.com/QIANLING-0831/--meeting.git
cd .\--meeting
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

也可以双击 `安装依赖.bat`，完成后双击 `启动转写.bat`。页面默认打开 `http://127.0.0.1:8765`。

首次启动后：

1. 点击顶部“配置 API Key”，输入两次百炼 Key。
2. 填写公司、岗位和 JD，上传简历，生成面试工作区。
3. 核对候选人事实和项目描述并保存。
4. 在工作台勾选需要使用的本地知识库。
5. 选择会议正在使用的系统输出设备，按需开启“听取我的回答”，点击“开始监听”。

Key 保存在被 Git 忽略的本机 `config.json`，页面只回显脱敏值。也可以使用 `DASHSCOPE_API_KEY` 环境变量。

## Qwen 设置

- `单模型`：默认模式，使用 `qwen-audio-3.0-realtime-plus` 直接听音频并回答。
- `双模型`：实验模式，`qwen-audio-3.0-asr-flash-streaming → qwen-plus`，可测试专业词热词增强。
- `Qwen Flash`：双模型模式下可作为更快的回答模型切换。
- `Qwen Audio Realtime Flash`：单模型模式下可切换，用于对比响应速度。
- `Server VAD`：本分支默认使用低灵敏度阈值 `0.2` 和 900ms 停顿，优先保证腾讯会议中的弱音与长句不丢失。
- `Smart Turn`：仍可手动选择；它会过滤被判定为无效语义的片段，不建议作为会议转写的默认模式。
- 百炼业务空间 ID：填写后使用业务空间专属北京地域名；留空使用兼容域名。

双模型会比理想状态下的单模型多出一次“完整句确认 → 回答请求”的往返，通常是几百毫秒到约 1 秒；网络和停顿判定会让实际值浮动。它也会同时产生 ASR 与文本模型费用，但避免了 Audio Realtime 对历史音频上下文的重复计费，长面试通常不会因此显著增费。

最后一个浏览器页面关闭超过 2 秒后，音频采集和 Qwen WebSocket 会自动停止。后台本地网页服务仍保留，可重新打开页面。

开始前可点击“检测会议声音”。程序会并行测量全部系统输出设备并选择真正有声音的一路；点击“开始”时还会再做一次短探测。弱音不会再被本地静音门限直接清零，页面也会显示 Qwen 的增量转写、环境转写和无效话轮提示。

## 本地数据

- 面试资料与文字记录：`workspace/sessions/<会话>/`
- 本机配置：`config.json`
- 服务仅监听 `127.0.0.1`

这些路径已被 Git 忽略，不会随正常提交上传。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

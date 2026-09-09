# Windows 系统声音转中文：可复用项目调研

调研日期：2026-09-09。结论仅依据项目自己的 README、源码/依赖、Release 和许可证；GitHub 活跃度数据会随时间变化。

## 结论

如果当前目标只是“播放浏览器/会议声音，马上看到中文字幕”，最快的验证方式不是再装 Python 项目，而是先按 `Win + Ctrl + L` 使用 Windows 11 自带实时字幕。它能读取默认声音输出、支持简体中文，并且语言包下载后可离线运行；处理在设备本地完成。但它不会保存字幕，也没有适合后续接 Codex 的公开文本输出，因此只能作为第一阶段的可用替代品，不能作为 Interview Copilot 最终管线。

如果需要“字幕文本还能继续送给 Codex”，推荐顺序是：

1. **先试 `local-meeting-stt` 的便携版**：它最贴近目标，有 Windows 系统回环、实时文本面板、保存 transcript、CPU/Vulkan/CUDA 多种后端；已有便携 Release。
2. **若希望直接复用当前 Python 工程，借鉴 `realtime-captions-system-audio`**：它的结构与我们的阶段一几乎完全相同，而且用 `PyAudioWPatch` 捕获 WASAPI 回环，正好可替换当前容易报 `data discontinuity` 的 `soundcard` 路径。项目很年轻，适合借代码，不宜无验证地整体依赖。
3. **若接受较重安装，试 `MeetingBro`**：中文支持写得最明确，已有完整 UI、实时字幕、系统音频和导出，但要同时准备 Python 与 Node.js，集成面较大。
4. **`VoiceFlow` 适合作为稳定成品和工程参考**：Windows 安装器、faster-whisper、本地多语言和 WASAPI 系统音频都齐全，活跃度也高；不过会议功能的说明更偏“长录音完成后转写/导出”，不是明确的逐句悬浮实时字幕，所以不排在第一位。

## 一眼比较

| 方案 | Windows 系统声音 | 中文 | 本地/离线 | 实时显示 | 文本保存/后续接 Codex | 安装难度 | 活跃度/成熟度 | 许可 |
|---|---|---|---|---|---|---|---|---|
| Windows 11 实时字幕 | 是，监听默认输出 | 明确支持简中/繁中 | 是（首次下载语言包） | 系统字幕窗 | **否**，官方说明生成字幕不存储 | 最低 | Windows 内置 | 非开源组件 |
| `kuchris/local-meeting-stt` | 是，WASAPI/自定义 Vulkan loopback | Whisper 多语言；默认配置偏日语 | 是 | 有实时面板 | 是，保存 `live_transcript.txt` 和 WAV | 低（下载便携 zip） | 很新：21 commits、v0.2.0、约 1 star | Apache-2.0 |
| `emidium-science/realtime-captions-system-audio` | 是，PyAudioWPatch/WASAPI | Auto-detect 多语言；应选非 `.en` 模型 | 是 | 终端 + 悬浮字幕 | 是，时间戳 txt | 中（Python 3.11 + pip） | 很新：4 commits、无 Release、约 9 stars | MIT（README 声明） |
| `armpro24-blip/MeetingBro` | Windows 原生系统音频 | **明确可选 Chinese** | 转写本地；摘要 LLM 可选 | UI + 约 1.5 秒预览 | 是，Markdown/历史记录 | 中高（Python + Node + 前后端） | 74 commits、约 35 stars；Release 页暂无成品包 | MIT |
| `infiniV/VoiceFlow` | 是，Windows WASAPI loopback | Whisper 自动检测 99+ 语言 | 是；摘要可关闭或走 Ollama | 字典听写实时；会议字幕实时性未明确 | 是，MD/TXT/SRT/JSON | 低（Windows `.exe`） | 较强：119 commits、约 417 stars、多次 Release，当前页列 v1.6.2 | MIT |
| `imnotwallace/LocalScribe` | 是，可分麦克风/远端系统流 | 依赖 Whisper，但 README 未明确承诺中文 | 是、无网络代码 | 数秒级近实时 UI | 是，JSON/MD/TXT/DOCX | 低到中（自包含安装器约 1.36 GB） | 代码量和测试较多；README 称 0.9.0，但 GitHub Release 页目前显示无 Release，需谨慎 | MIT |

## 候选项目细节

### 0. Windows 11 内置实时字幕：今天就能试

微软官方说明实时字幕适用于 Windows 11 22H2 及以上，可把通过电脑的语音显示为字幕，关注系统设置中的默认声音输出设备；简体中文（中国）和繁体中文都在语音识别语言列表中，音频和字幕处理在设备上进行，下载语言文件后可断网使用。开启快捷键是 `Win + Ctrl + L`。[微软官方使用说明](https://support.microsoft.com/zh-cn/windows/%E4%BD%BF%E7%94%A8%E5%AE%9E%E6%97%B6%E5%AD%97%E5%B9%95%E6%9B%B4%E5%A5%BD%E5%9C%B0%E4%BA%86%E8%A7%A3%E9%9F%B3%E9%A2%91-b52da59c-14b8-4031-aeeb-f6a47e6055df)

局限也很明确：官方说明生成字幕不存到设备或云端。这意味着它适合验证“系统声音能不能转中文”，但不便作为下一阶段问题切分/Codex 输入源。

### 1. `kuchris/local-meeting-stt`：最值得先下载试用

项目专门面向 Windows 的 Teams、浏览器会议和桌面音频：系统扬声器/耳机输出经 loopback 捕获，可选混入麦克风，实时面板显示带时间戳文本，并保存 WAV 与 `live_transcript.txt`。README 推荐先试 `CPP Vulkan LB Base`，也提供 faster-whisper 的 `Live Text` / `Live + WAV` 路径。[README](https://github.com/kuchris/local-meeting-stt)

它已有 v0.1.0、v0.2.0 便携 Release；v0.2.0 加入 Vulkan loopback 与便携后端运行时。下载 zip、解压、运行 EXE，再在 Setup 下载模型并选择输出设备即可。[Releases](https://github.com/kuchris/local-meeting-stt/releases)

风险：项目规模和用户量都很小；默认语言偏日语，虽然 Whisper 后端本身支持多语言，仍应拿实际中文视频做 10 分钟稳定性和断句测试。许可证为 Apache-2.0。[LICENSE](https://github.com/kuchris/local-meeting-stt/blob/master/LICENSE)

### 2. `realtime-captions-system-audio`：最合适的代码参考

项目目标与阶段一完全重合：Windows 11 WASAPI loopback、Whisper/RealtimeSTT、实时 partial text、句末稳定文本、终端历史、置顶点击穿透字幕窗、时间戳 txt，CPU 也可运行。[README](https://github.com/emidium-science/realtime-captions-system-audio)

对当前代码最有价值的是捕获层：它明确依赖 `pyaudiowpatch>=0.2.12.8`，而不是 `soundcard`；其架构把 48 kHz 立体声回环重采样成 16 kHz 单声道后喂给 RealtimeSTT，并把捕获、识别、刷新 UI、悬浮窗放到独立线程。[requirements.txt](https://github.com/emidium-science/realtime-captions-system-audio/blob/main/requirements.txt)；[architecture.md](https://github.com/emidium-science/realtime-captions-system-audio/blob/main/docs/architecture.md)

中文配置要注意：README 的默认推荐模型是 `tiny.en` / `small.en`，这些只能识别英文。中文必须选自动检测并使用不带 `.en` 的 multilingual 模型。项目只有少量提交且没有 Release，建议借鉴或移植捕获/UI，不把它当成熟发行版直接托底。README 声明 MIT。

### 3. `MeetingBro`：功能完整、中文明确，但安装较重

它在 Windows 直接抓系统音频，Whisper 本地实时转写，可在 UI 中明确选择 `Chinese`，约 1.5 秒的 streaming preview 先给出快字幕、主转写再纠正；不配 API Key 仍能完成转写，全文和笔记可导出 Markdown。[README](https://github.com/armpro24-blip/MeetingBro)；[中文 README](https://github.com/armpro24-blip/MeetingBro/blob/main/README.zh-CN.md)

代价是安装脚本会准备 Python 3.12、Node.js、Python 后端依赖与前端 npm 依赖，运行两个服务；这比当前单文件 Python 原型复杂。仓库采用 MIT 许可证，但 [Releases](https://github.com/armpro24-blip/MeetingBro/releases) 当前没有可直接下载的正式包。

### 4. `VoiceFlow`：最像成熟可安装产品

VoiceFlow 提供 Windows 10/11 `.exe` 安装器，以 faster-whisper 在本地推理，CPU/CUDA 均可，并声称支持 Whisper 自动检测的 99+ 语言。v1.6 系列加入 Windows WASAPI loopback，将麦克风和系统音频录成双声道，支持重新转写和 MD/TXT/SRT/JSON 导出。[README](https://github.com/infiniV/VoiceFlow)；[Releases](https://github.com/infiniV/VoiceFlow/releases)

它更适合“直接装一个能录会议并转写的成品”。但官方会议说明围绕录制、完成转写、回放和导出，没有明确承诺把系统音频逐句实时显示成字幕；因此，在面试现场要求低延迟文字时要先实测。MIT 许可。

### 5. `LocalScribe`：工程设计好，但当前分发状态矛盾

LocalScribe 是 Windows 11 WPF 本地会议工具：麦克风和远端音频分流、VAD 后本地 Whisper、数秒级文本、说话方结构化区分、可导出 JSON/Markdown/文本等。[README](https://github.com/imnotwallace/LocalScribe)

README 声称提供约 1.36 GB 的未签名 0.9.0 安装器，但仓库的 [Releases 页面](https://github.com/imnotwallace/LocalScribe/releases) 当前又显示没有 Release。这个矛盾需要在采用前向作者确认或自行从源码构建。README 也没有明确列中文支持，因此目前更适合参考它的双流、VAD、故障恢复设计。MIT 许可。

## 底层引擎判断

### 继续用 `faster-whisper` 是合理的

官方项目采用 MIT 许可，支持 CPU `int8`，并说明 GPU 需要 CUDA 12 的 cuBLAS 和 cuDNN 9；这正好解释了此前 `cublas64_12.dll` 缺失错误。当前机器若不准备完整 CUDA 运行库，应固定 `device="cpu", compute_type="int8"`，不要自动选 CUDA。[faster-whisper README](https://github.com/SYSTRAN/faster-whisper)

### `whisper.cpp` 是后续打包的备选，不是完整捕获/UI 方案

`whisper.cpp` 为 MIT，支持 CPU、Vulkan、CUDA、OpenVINO 等多种后端，适合把依赖做得更可控；但官方的实时示例只描述 SDL2 麦克风输入，并没有直接提供 Windows 系统音频回环和字幕 UI。所以它应作为识别引擎嵌入上层项目，而不是直接替代本应用。[whisper.cpp README](https://github.com/ggml-org/whisper.cpp)

## `zero2Agent` 的定位

[`ranxi2001/zero2Agent`](https://github.com/ranxi2001/zero2Agent) 与音频捕获、Whisper 或实时转写无关。它是 Agent 工程教程与面试材料仓库，包含 Agent 基础、LangGraph、SDK、Codex CLI、面试题和《Agent 面试 500 问》等内容，适合未来放进 `knowledge/` 做检索知识库，不应参与阶段一音频管线。

仓库根 LICENSE 是 MIT，但 README 对发布的绿皮书另外标注 `CC BY-NC-SA 4.0`。因此如果未来复制或转换知识内容，应保留来源/许可元数据，并特别确认 PDF/文章是否受非商业和相同方式共享条件约束，而不能只看根目录 MIT。[根 LICENSE](https://github.com/ranxi2001/zero2Agent/blob/main/LICENSE)；[README](https://github.com/ranxi2001/zero2Agent)

## 对当前项目的下一步建议

1. 先让用户按 `Win + Ctrl + L`，把语言切为中文，验证电脑对同一浏览器视频能稳定显示系统字幕。这一步不修改项目，几分钟能判断 Windows 音频路径是否正常。
2. 同时或随后下载 `local-meeting-stt` v0.2.0 便携版做 A/B 测试：同一段 10 分钟中文视频，观察首次出字延迟、断句、漏字、CPU 占用和输出文件。
3. 若现有 Python 原型要继续保留，下一次代码迭代优先移植 `realtime-captions-system-audio` 的 `PyAudioWPatch` WASAPI 捕获与悬浮窗思路；继续保留 faster-whisper CPU int8，避免再次触发 CUDA DLL 依赖。
4. 达到“持续出字 + 可保存”后，再做完整问题切分与 Codex CLI；不要在捕获层尚不稳定时提前叠加后续模块。

## 专项结论：FFmpeg 能否在 Windows 直接抓系统声音

### 先说结论

截至本次调研所见的 FFmpeg 当前官方设备文档与 trunk 源码，**官方 FFmpeg 没有原生 `wasapi` 输入设备，也没有可直接打开扬声器渲染端点的 `-f wasapi -loopback ...` 用法**。Windows 音频输入由 `dshow`（DirectShow）提供；`dshow` 只能打开系统已经暴露为“录音/捕获设备”的音频源。[FFmpeg Devices 文档](https://ffmpeg.org/ffmpeg-devices.html#dshow)；[当前 trunk 的 `dshow.c`](https://ffmpeg.org/doxygen/trunk/dshow_8c.html)

所以，普通官方 Windows FFmpeg 要录“电脑正在播放的声音”，必须满足以下之一：

- 声卡驱动提供并启用了 `Stereo Mix` / `Waveout Mix` / `What You Hear` 等硬件回环录音设备；或
- 用户安装虚拟声卡，并把浏览器/会议软件的输出路由到它，再由 FFmpeg 把该虚拟声卡的录音端当作 DirectShow 输入。

这不是 WASAPI loopback 本身的限制。微软说明真正的 WASAPI loopback 是在**渲染端点**上以共享模式和 `AUDCLNT_STREAMFLAGS_LOOPBACK` 初始化捕获流；即使声卡没有 Stereo Mix 之类的硬件回环设备，WASAPI 也仍可捕获系统混音。[Microsoft：Loopback Recording](https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording)；[`IAudioClient::Initialize`](https://learn.microsoft.com/en-us/windows/win32/api/audioclient/nf-audioclient-iaudioclient-initialize)

因此网上若出现 `ffmpeg -f wasapi ...`，不能假定官方发行版可用；应先以本机 `ffmpeg -devices` 的输出为准。对当前项目而言，**FFmpeg 不能替代 PyAudioWPatch 来实现“无需 Stereo Mix/虚拟声卡的通用 WASAPI loopback”**。

### 在已有 Stereo Mix 或虚拟声卡时，推荐的 FFmpeg 管线

第一步列出 DirectShow 设备：

```powershell
ffmpeg -hide_banner -list_devices true -f dshow -i dummy
```

FFmpeg 官方文档把这条命令作为 `dshow` 设备枚举方式，并说明 `audio_buffer_size` 直接影响延迟；默认通常是若干个 500 ms，设得太低又可能降低稳定性。[FFmpeg `dshow` 文档](https://ffmpeg.org/ffmpeg-devices.html#dshow)

确认设备名后，将声音转成 Whisper 需要的 16 kHz、单声道、16-bit PCM，并从 stdout 持续交给 Python：

```powershell
ffmpeg -hide_banner -loglevel warning `
  -fflags nobuffer `
  -f dshow -audio_buffer_size 100 `
  -i audio="Stereo Mix (Realtek(R) Audio)" `
  -vn -ac 1 -ar 16000 -c:a pcm_s16le `
  -flush_packets 1 -f s16le pipe:1
```

设备名只是示例，必须使用枚举得到的本机名称。`-fflags nobuffer` 只减少输入分析阶段的额外缓冲，`-flush_packets 1` 倾向于立即写出输出包；它们无法消除 DirectShow 驱动缓冲或 Whisper 等待完整语音段的时间。[FFmpeg Formats 文档](https://ffmpeg.org/ffmpeg-formats.html)；[FFmpeg Pipe protocol](https://ffmpeg.org/ffmpeg-protocols.html#pipe)

Python 端应启动一个长期存在的 FFmpeg 子进程，从 `stdout` 按固定长度读取原始 PCM，`stderr` 单独消费以免管道阻塞。16 kHz × 单声道 × 16-bit 的数据率是 32,000 byte/s；例如每次读 3,200 bytes 就是约 100 ms 音频。不要每句话重启 FFmpeg，也不要先落 WAV 再识别，否则启动和磁盘轮询会制造额外延迟。

### 字幕分段与预期延迟

FFmpeg 只负责捕获、重采样和输出 PCM，**不会判断一句话何时结束**。实时字幕仍需在应用层做分段。建议：

1. 保留约 200–300 ms pre-roll，避免 VAD 切掉第一个字。
2. 检测到语音后累计 PCM；连续静音约 600–800 ms 时提交稳定段。
3. 单段设置约 12–15 秒上限，长句到上限强制提交；相邻段保留 300–500 ms overlap，再按时间戳或公共前缀去重。
4. 如果需要“边说边出现”，每约 1 秒对最近 4–6 秒滚动窗口做一次 provisional 转写；停顿后再用完整段生成 final 文本。UI 必须区分会变化的临时字幕与已经提交的稳定字幕。
5. faster-whisper 可输出 word timestamps，并内置 Silero VAD；其默认 VAD 很保守，只移除超过 2 秒的静音，官方示例允许把 `min_silence_duration_ms` 调成 500 ms。因此面试字幕应显式设置约 500–800 ms，不能依赖默认值。[faster-whisper：word timestamps 与 VAD](https://github.com/SYSTRAN/faster-whisper#word-level-timestamps)

延迟应拆开看：

- FFmpeg/DirectShow 捕获与 pipe：目标约 0.1–0.6 秒，取决于 `audio_buffer_size`、驱动和读取粒度；FFmpeg 官方明确说默认设备缓冲通常是 500 ms 的倍数。
- 句末等待：约 0.6–0.8 秒，这是为了获得可靠断句而主动加入的静音阈值。
- Whisper 推理：随 CPU/GPU、模型和段长变化。faster-whisper 官方在 i7-12700K 上给出的 small/int8 CPU 基准是 13 分钟音频约 1 分 42 秒，说明合适硬件上推理可以快于实时，但这不是对任意机器的延迟保证。[faster-whisper benchmark](https://github.com/SYSTRAN/faster-whisper#benchmark)

据此做工程预期：使用 `base` 或 `small`、CPU int8，在性能正常的近年 Windows 电脑上，**final 字幕通常应以句末后约 1–3 秒为目标**；较慢 CPU 或较长片段可能达到 3–6 秒。若做滚动 provisional 字幕，可争取开口后约 1–2 秒开始显示，但文字会被后续上下文修正。这些是由上述缓冲、静音门限与推理速度推算的目标区间，最终必须在本机用固定中文样本测量 P50/P95。

`whisper.cpp` 官方的 `whisper-stream` 示例也是每 500 ms 采样、用 5 秒窗口连续重跑推理，说明 Whisper 的“实时”通常是滚动窗口近实时，而不是模型原生逐 token 音频流。该官方示例只抓麦克风，不提供 Windows 系统回环。[whisper.cpp real-time example](https://github.com/ggml-org/whisper.cpp#real-time-audio-input-example)

### 与 PyAudioWPatch / soundcard 的利弊

| 捕获方案 | 优点 | 主要问题 | 对本项目的适合度 |
|---|---|---|---|
| FFmpeg + DirectShow | 重采样、声道转换、格式输出成熟；子进程隔离，崩溃可重启；很容易同时保存音频；Python 只读 PCM | 官方版没有 WASAPI loopback；依赖 Stereo Mix 或虚拟声卡和额外路由；DirectShow 默认缓冲偏大；还要管理子进程/stdout/stderr | **仅当本机已有稳定 Stereo Mix/虚拟声卡时采用** |
| PyAudioWPatch | 直接枚举/打开 WASAPI loopback 渲染端点；无需虚拟声卡；与当前 Python 队列/VAD/Whisper 最短路径集成 | Python 音频依赖仍需处理设备切换、缓冲溢出和线程退出；保存/重采样需自己实现或调用库 | **当前首选** |
| soundcard | 当前代码已接入，接口简单，也能看到 loopback 设备 | 本机已经实际出现重复 `data discontinuity in recording` 警告；抑制警告不等于恢复丢失的采样，难以确认长时间连续性 | **不建议继续作为最终捕获层** |

微软还指出，Stereo Mix 等硬件回环设备并非所有声卡都有、可能默认禁用，而且厂商命名不统一；与之相对，WASAPI loopback 不要求硬件暴露这种录音设备。这是 PyAudioWPatch 在“发给普通 Windows 用户直接用”场景中的关键优势。[Microsoft：硬件 loopback 与 WASAPI loopback 对比](https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording)

### 对当前项目的明确建议

1. **不要把 FFmpeg 作为默认系统声音捕获器。** 官方 FFmpeg 缺少原生 WASAPI loopback，若为了它要求用户安装虚拟声卡，会让第一阶段更难使用。
2. 默认管线改为 `PyAudioWPatch WASAPI loopback → 16 kHz mono PCM → VAD/滚动窗口 → faster-whisper CPU int8 → 字幕窗口`。这与前一节调研到的 `realtime-captions-system-audio` 结构一致。
3. FFmpeg 保留为可选适配器：启动时若 `dshow` 枚举到 Stereo Mix 或用户明确配置了虚拟声卡，可选择 `FFmpegDirectShowCapture`；它尤其适合“同时保存完整 WAV/FLAC”或调试音频格式。
4. 下一步最小实现应先替换 `soundcard` 捕获层并加入 10 分钟连续性测试，记录丢帧、队列积压、首字延迟和句末 final 延迟；在这些指标稳定前，不接问题切分或 Codex。

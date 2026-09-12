# Qwen Realtime Interview Copilot

这个分支是单通道实验版：Windows 系统声音直接发送给 Qwen-Audio Realtime，由同一个模型完成输入转写、话轮判断和流式回答。运行链路不启动 Paraformer，也不调用 Codex。

> 使用前请取得面试参与者同意，并遵守当地法律、公司政策和面试规则。程序不保存原始音频，只在本机保存文字记录。

## 数据链路

```text
会议系统声音 → Qwen-Audio Realtime → 问题转写 + 流式回答 → 浏览器
```

Qwen 返回的输入转写直接显示在左侧，因此不会再出现 Paraformer 与 Qwen 两套话轮进度不一致的问题。浏览器 WebSocket 会一次排空已经到达的事件，避免按固定速率逐条发送造成积压。

## 面试资料

准备面试时可以上传 PDF、Markdown 或 TXT 简历，填写 JD，并在“候选人事实与项目描述”中核对或补充项目经历。启动实时会话时，这些内容一次性写入 Qwen 的会话指令：

- 每道题无需额外数据库查询或 Function Calling 往返，速度优先。
- 资料更新后需要停止并重新开始实时会话才会生效。
- 超长资料目前会截取 JD 前 12,000 字、候选人事实前 18,000 字。

这不是完整 RAG。若以后资料量明显增大，可以再增加按需检索；那会触发工具调用和第二轮模型推理，通常会增加可感知延迟。

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
4. 选择会议正在使用的系统输出设备，点击“开始”。

Key 保存在被 Git 忽略的本机 `config.json`，页面只回显脱敏值。也可以使用 `DASHSCOPE_API_KEY` 环境变量。

## Qwen 设置

- `qwen-audio-3.0-realtime-plus`：本分支默认，回答能力优先。
- `qwen-audio-3.0-realtime-flash`：可以在页面切换，用于对比速度。
- `Smart Turn`：结合声学和语义判断问题是否说完，默认推荐。
- `Server VAD`：按服务端静音检测切分话轮。
- 百炼业务空间 ID：填写后使用业务空间专属北京地域名；留空使用兼容域名。

最后一个浏览器页面关闭超过 2 秒后，音频采集和 Qwen WebSocket 会自动停止。后台本地网页服务仍保留，可重新打开页面。

## 本地数据

- 面试资料与文字记录：`workspace/sessions/<会话>/`
- 本机配置：`config.json`
- 服务仅监听 `127.0.0.1`

这些路径已被 Git 忽略，不会随正常提交上传。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

# Interview Copilot

Windows 本地面试辅助工具：阿里云 Paraformer 将系统声音和可选麦克风实时转写，本地知识库先返回相关要点，再通过你已经登录的 Codex / ChatGPT Plus 生成简洁、可口述的回答。无需购买 OpenAI API。

> 使用前请取得面试参与者同意，并遵守当地法律、公司政策和面试规则。本工具不保存原始音频，只保存本地文字记录。

## 当前能力

- 系统声音标记为“面试官”，麦克风标记为“我”；麦克风默认关闭，可在页面中打开。
- 上传本次简历和 JD，并逐场选择多个知识库。
- 简历先在本地提取并移除常见手机号、邮箱、证件号，确认后才参与回答。
- 默认自动识别问题；本地检索要点先显示，Codex 完整回答随后流式显示。
- F8 手动回答当前问题，Esc 打断当前生成。
- 每场面试分别保存面试官转写、候选人转写、回答和已确认事实。
- `zero2Agent` 保留为原始资料仓，同时建立 SQLite FTS5 本地索引。

## 安装与配置

需要 Windows 10/11、Python 3.11+、Codex 桌面端或 Codex CLI，以及阿里云百炼 API Key。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

推荐启动页面后点击顶部“配置 Paraformer”，手动输入两遍并确认。Key 会保存在已被 Git 忽略的本机 `config.json` 中，之后自动作为默认值；页面只显示脱敏尾号。该文件仍是本机明文配置，请勿发送给他人。

也可以继续使用当前 Windows 用户的环境变量：

```powershell
[Environment]::SetEnvironmentVariable("DASHSCOPE_API_KEY", "你的 API Key", "User")
```

环境变量方式需要重新打开终端或 Codex 后生效；页面保存的本机默认值优先。Codex 登录无需 OpenAI API Key：页面会读取本机 Codex 登录态；若未登录，点击“登录 Codex”并在新页面中亲自完成一次 ChatGPT 授权。

## 知识库

默认 Agent 面试资料位于：

```text
workspace/knowledge/sources/zero2Agent/learn-agent-interview/
```

其他岗位资料按知识包放置：

```text
workspace/knowledge/packs/
├── java/
│   ├── Java八股.md
│   └── JVM.md
├── backend/
│   ├── MySQL.md
│   └── Redis.md
└── llm/
    ├── RAG.md
    └── Agent.md
```

支持 `.md`、`.txt`、`.pdf`。添加资料后，在页面点击“重建索引”；面试准备时可同时勾选多个知识包。

## 运行

双击 `启动转写.bat`，或运行：

```powershell
.\.venv\Scripts\python.exe app.py
```

浏览器默认打开 `http://127.0.0.1:8765`。首次使用流程：

1. 检查页面顶部 Paraformer 与 Codex 状态。
2. 填写公司、岗位和 JD，按需上传简历、勾选知识库。
3. 生成工作区，核对并保存候选人事实。
4. 点击“开始”；按需开启麦克风识别。

只列出系统声音设备：

```powershell
.\.venv\Scripts\python.exe app.py --list-devices
```

旧版控制台转写仍可运行：

```powershell
.\.venv\Scripts\python.exe app.py --console
```

## 数据位置与边界

- 文字记录：`workspace/sessions/<会话>/`
- 知识索引：`workspace/knowledge/indexes/knowledge.db`
- 原始知识资料：`workspace/knowledge/sources/` 与 `workspace/knowledge/packs/`
- Codex 以只读工作区、禁止审批的方式启动回答线程；它不会修改项目文件。
- 页面只监听本机 `127.0.0.1`，不对局域网公开。
- 蓝牙耳机进入“免提/通话”模式时 Windows 可能切换设备，需要停止后重启识别。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

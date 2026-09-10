# Interview Copilot

Windows 本地面试辅助工具：阿里云 Paraformer 将系统声音和可选麦克风实时转写，本地知识库先返回相关要点，再通过使用者已经登录的 Codex 生成简洁、可口述的回答。本项目不要求填写 OpenAI API Key。

> 使用前请取得面试参与者同意，并遵守当地法律、公司政策和面试规则。本工具不保存原始音频，只在本机保存文字记录。

## 当前能力

- 系统声音标记为“面试官”，麦克风标记为“我”；麦克风默认关闭，可在页面中打开。
- 上传本次简历和 JD，并逐场选择多个知识库。
- 简历先在本地提取并移除常见手机号、邮箱、证件号，确认后才参与回答。
- 默认自动识别问题；本地检索要点先显示，Codex 完整回答随后流式显示。
- 可手动选择多条转写组成问题；F8 回答当前问题，Esc 打断生成。
- 每场面试分别保存面试官转写、候选人转写、回答和已确认事实。
- 支持导入 `zero2Agent`，也支持按岗位创建 Markdown、TXT、PDF 知识包。

## 三步本地部署（Windows）

需要 Windows 10/11、Python 3.11+、Codex CLI（使用者自己的 ChatGPT/Codex 账户）和阿里云百炼 API Key。

```powershell
git clone https://github.com/QIANLING-0831/--meeting.git
cd .\--meeting
```

1. 双击 `安装依赖.bat`，自动创建 `.venv` 并安装 Python 依赖。
2. 首次使用先在终端运行 `codex`，选择 **Sign in with ChatGPT** 完成登录；安装与登录方式见 [Codex CLI 官方说明](https://developers.openai.com/codex/cli)。
3. 双击 `启动转写.bat`，浏览器会自动打开 `http://127.0.0.1:8765`。

如果不想使用批处理，也可以手动安装：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Paraformer 配置

启动页面后点击顶部“配置 Paraformer”，手动输入两遍并确认。Key 会保存在已被 Git 忽略的本机 `config.json` 中，之后自动作为默认值；页面只显示脱敏尾号。该文件仍是本机明文配置，请勿发送给他人。

也可以使用当前 Windows 用户的环境变量：

```powershell
[Environment]::SetEnvironmentVariable("DASHSCOPE_API_KEY", "你的 API Key", "User")
```

环境变量方式需要重新打开终端后生效；页面保存的本机默认值优先。Codex 登录无需在本项目中填写 OpenAI API Key，页面读取当前 Windows 用户的 Codex 登录态。每位部署者必须使用自己的账户，仓库不包含共享登录信息。

## 知识库

仓库不会打包第三方题库。需要 `zero2Agent` 时，在项目根目录运行：

```powershell
git clone https://github.com/ranxi2001/zero2Agent.git workspace/knowledge/sources/zero2Agent
```

默认读取目录：

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

支持 `.md`、`.txt`、`.pdf`。添加资料后，在页面点击“重建索引”；面试准备时可同时勾选多个知识包。第三方资料受其原仓库许可证约束。

## 首次使用

1. 检查页面顶部 Paraformer 与 Codex 状态。
2. 填写公司、岗位和 JD，按需上传简历、勾选知识库。
3. 生成工作区，核对并保存候选人事实。
4. 点击“开始”；按需开启麦克风识别。

只列出系统声音设备：

```powershell
.\.venv\Scripts\python.exe app.py --list-devices
```

启动但不自动打开浏览器：

```powershell
.\.venv\Scripts\python.exe app.py --no-browser
```

旧版控制台转写：

```powershell
.\.venv\Scripts\python.exe app.py --console
```

## 常见部署问题

- `Python environment not found`：先双击 `安装依赖.bat`。
- `codex 不是内部或外部命令`：安装 Codex CLI，重新打开终端后运行 `codex --version`。
- Codex 显示未登录：在终端运行 `codex`，使用部署者自己的 ChatGPT 账户登录，然后重启本项目。
- 页面打不开：检查启动窗口是否报错，以及本机端口 `8765` 是否被占用。
- 没有系统声音设备：将 Windows 输出切换到实际使用的扬声器或耳机，再重启识别。
- 蓝牙耳机进入“免提/通话”模式时 Windows 可能切换设备，需要停止后重启识别。
- 修改过依赖：再次运行 `安装依赖.bat` 即可更新当前虚拟环境。

## 本地数据与隐私边界

- 文字记录：`workspace/sessions/<会话>/`
- 知识索引：`workspace/knowledge/indexes/knowledge.db`
- 原始知识资料：`workspace/knowledge/sources/` 与 `workspace/knowledge/packs/`
- Codex 以只读工作区、禁止审批的方式启动回答会话，不会修改项目文件。
- 页面只监听本机 `127.0.0.1`，不对局域网公开。
- `config.json`、面试记录、知识库原文及索引均已被 Git 忽略，不会随正常提交上传。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

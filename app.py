import argparse
from pathlib import Path
import threading
import webbrowser

from interview_copilot.audio import list_loopback_devices
from interview_copilot.config import AppConfig
from interview_copilot.ui import InterviewCopilotApp


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Codex 中文面试助手")
    parser.add_argument("--list-devices", action="store_true", help="列出系统声音回环设备后退出")
    parser.add_argument("--console", action="store_true", help="使用旧版控制台转写界面")
    parser.add_argument("--no-browser", action="store_true", help="启动 Web 服务但不自动打开浏览器")
    args = parser.parse_args()
    if args.list_devices:
        for index, device in enumerate(list_loopback_devices(), start=1):
            print(f"{index}. {device.name}")
    elif args.console:
        InterviewCopilotApp().run()
    else:
        import uvicorn

        root_dir = Path(__file__).resolve().parent
        config = AppConfig.load(root_dir)
        if not args.no_browser:
            url = f"http://{config.web_host}:{config.web_port}"
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        uvicorn.run(
            "interview_copilot.web:create_app",
            host=config.web_host,
            port=config.web_port,
            factory=True,
        )

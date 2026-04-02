import sys
import os
import json
import asyncio
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThread, Signal, QObject

from front_end import OCRMainWindow
from back_end import run_backend

CONFIG_FILE = "config.json"

class EmittingStream(QObject):
    textWritten = Signal(str)

    def write(self, text):
        self.textWritten.emit(str(text))

    def flush(self):
        pass

class BackendThread(QThread):
    new_record_added = Signal()

    def __init__(self, watch_dir, db_path, token):
        super().__init__()
        self.watch_dir = watch_dir
        self.db_path = db_path
        self.token = token

    def run(self):
        # 封装闭包以越级投递信号触发事件
        def trigger_signal():
            self.new_record_added.emit()
            
        asyncio.run(run_backend(self.watch_dir, self.db_path, self.token, trigger_signal))

def load_initial_config():
    # 程序入口处的基础配置嗅探
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    # 直接将配置文件名注入主窗口让其自行接管状态
    window = OCRMainWindow(CONFIG_FILE)
    
    stream = EmittingStream()
    stream.textWritten.connect(window.append_log)
    sys.stdout = stream
    sys.stderr = stream
    
    config = load_initial_config()
    watch_dir = config.get("watch_dir", "")
    db_path = config.get("db_path", "")
    token = config.get("api_token", "")
    
    # 防御性判断阻断非完整环境下的后端加载
    if watch_dir and db_path and token:
        thread = BackendThread(watch_dir, db_path, token)
        thread.new_record_added.connect(window.load_data_from_db)
        thread.start()
    else:
        window.append_log("系统提示: 缺少必要配置。\n请前往「设置」页面配置 数据库路径、监控目录 以及 API Token，配置完成后请重启本软件。")

    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
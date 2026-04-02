import asyncio
import os
import base64
import requests
import aiosqlite
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

API_URL = "https://99j5u47emfs4r2l5.aistudio-app.com/layout-parsing"

class NewImageHandler(FileSystemEventHandler):
    def __init__(self, loop, queue):
        self.loop = loop
        self.queue = queue
        self._last_processed = {}

    #override了创建时方法
    def on_created(self, event):
        #过滤出图片文件、通过lastprocessed检测防抖处理
        if not event.is_directory and event.src_path.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp')):
            current_time = time.time()
            if event.src_path in self._last_processed:
                if current_time - self._last_processed[event.src_path] < 2.0:
                    return
            
            #使用asyncio的call_soon_threadsafe跨线程访问异步队列
            #使用了asyncio的put_nowait将图片后缀的文件路径event.src_path非阻塞入队
            self._last_processed[event.src_path] = current_time
            self.loop.call_soon_threadsafe(self.queue.put_nowait, event.src_path)

# 构造请求头将动态传入的凭据拼接到认证字段中
def process_ocr_sync(file_path, token):
    try:
        with open(file_path, "rb") as file:
            file_bytes = file.read()
            file_data = base64.b64encode(file_bytes).decode("ascii")

        headers = {
            "Authorization": f"token {token}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "file": file_data,
            "fileType": 1,
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
        }

        response = requests.post(API_URL, json=payload, headers=headers)
        if response.status_code != 200:
            return None
        return response.json().get("result")
    except Exception as e:
        print(f"请求失败: {e}")
        return None

async def init_db(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ocr_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_name TEXT NOT NULL,
                extracted_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

# 遍历版面分析结果，提取并拼接所有Markdown格式的纯文本，丢弃坐标与图片数据
def extract_text_from_result(result_dict):
    if not result_dict or "layoutParsingResults" not in result_dict:
        return ""
    
    full_text = []
    for res in result_dict.get("layoutParsingResults", []):
        if "markdown" in res and "text" in res["markdown"]:
            full_text.append(res["markdown"]["text"])
    
    return "\n".join(full_text)

# 挂载凭据参数以供同步请求函数调用
async def ocr_worker(queue, db_path, on_success_callback, token):
    
    while True:
        try:
            file_path = await queue.get()
            print(f"检测到新截图，开始识别: {file_path}")
            
            raw_result = await asyncio.to_thread(process_ocr_sync, file_path, token)
            
            if raw_result:
                extracted_text = extract_text_from_result(raw_result)
                
                if extracted_text.strip():
                    async with aiosqlite.connect(db_path) as db:
                        await db.execute(
                            "INSERT INTO ocr_records (file_name, extracted_text) VALUES (?, ?)",
                            (file_path, extracted_text)
                        )
                        await db.commit()
                    print(f"识别完成，纯文本已存入数据库: {file_path}")
                    
                    if on_success_callback:
                        on_success_callback()
                else:
                    print(f"未能从 {file_path} 中提取到有效文字。")
            
            queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"处理任务时发生错误: {e}")

# 接收顶级入口下发的凭据并分发给消费者协程
async def run_backend(watch_dir, db_path, token, on_success_callback=None):
    
    os.makedirs(watch_dir, exist_ok=True)
    await init_db(db_path)

    queue = asyncio.Queue()
    worker_task = asyncio.create_task(ocr_worker(queue, db_path, on_success_callback, token))

    loop = asyncio.get_running_loop()
    event_handler = NewImageHandler(loop, queue)
    observer = Observer()
    observer.schedule(event_handler, watch_dir, recursive=True)
    observer.start()
    print(f"已启动目录监控: {watch_dir}")

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        observer.stop()
        observer.join()
        worker_task.cancel()
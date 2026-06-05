import asyncio
import json
import os
import requests
import aiosqlite
import time
import traceback
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
MODEL = "PaddleOCR-VL-1.6"
MAX_POLL_SECONDS = 300  # 轮询超时上限（秒）
POLL_INTERVAL = 5      # 每次轮询间隔（秒），与官方样例一致

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

# 同步方式提交OCR作业并轮询结果（1.6 作业提交+轮询模式）
# 严格对齐官方 sample-1.6.py 的请求格式，使用 requests 库
def process_ocr_sync(file_path, token):
    try:
        if not os.path.exists(file_path):
            print(f"[诊断] 错误: 文件不存在 {file_path}")
            return None

        headers = {
            "Authorization": f"bearer {token}",
        }
        optional_payload = {
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
        }

        # ===== 提交作业（本地文件模式，与官方样例完全一致）=====
        data = {
            "model": MODEL,
            "optionalPayload": json.dumps(optional_payload)
        }
        
        print(f"[诊断] ========== 开始OCR流程 ==========")
        print(f"[诊断] 文件: {file_path}")
        print(f"[诊断] 文件大小: {os.path.getsize(file_path)} bytes")
        print(f"[诊断] API地址: {JOB_URL}")
        print(f"[诊断] 模型: {MODEL}")
        
        with open(file_path, "rb") as f:
            files = {"file": f}
            print(f"[诊断] 正在提交作业...")
            job_response = requests.post(JOB_URL, headers=headers, data=data, files=files)

        print(f"[诊断] 提交作业响应状态码: {job_response.status_code}")
        print(f"[诊断] 提交作业响应体: {job_response.text[:1000]}")

        if job_response.status_code != 200:
            print(f"提交作业失败 [{job_response.status_code}]: {job_response.text}")
            return None

        try:
            job_json = job_response.json()
        except json.JSONDecodeError:
            print(f"[诊断] 提交作业返回非法JSON: {job_response.text[:500]}")
            return None

        jobId = job_json.get("data", {}).get("jobId")
        if not jobId:
            print(f"[诊断] 提交作业响应缺少 jobId，完整响应: {job_json}")
            return None
        print(f"[诊断] 作业已提交, job id: {jobId}")

        # ===== 轮询作业状态 =====
        start_time = time.monotonic()
        poll_count = 0
        while True:
            elapsed = time.monotonic() - start_time
            if elapsed > MAX_POLL_SECONDS:
                print(f"[诊断] 轮询超时（{MAX_POLL_SECONDS}秒），作业可能仍在处理中, job id: {jobId}")
                return None

            poll_count += 1
            time.sleep(POLL_INTERVAL)
            
            poll_url = f"{JOB_URL}/{jobId}"
            print(f"[诊断] 第{poll_count}次轮询 (已等待{elapsed:.1f}s) URL: {poll_url}")
            job_result_response = requests.get(poll_url, headers=headers)
            
            print(f"[诊断] 轮询响应状态码: {job_result_response.status_code}")
            print(f"[诊断] 轮询响应体: {job_result_response.text[:500]}")

            if job_result_response.status_code != 200:
                print(f"查询作业状态失败 [{job_result_response.status_code}]")
                return None

            jr_json = job_result_response.json()
            state = jr_json.get("data", {}).get("state", "unknown")

            if state == "pending":
                print(f"作业状态: pending （已等待 {elapsed:.0f}s）")
            elif state == "running":
                try:
                    progress = jr_json["data"]["extractProgress"]
                    print(f"作业状态: running, 总页数: {progress.get('totalPages', '?')}, "
                          f"已提取: {progress.get('extractedPages', '?')}")
                except KeyError:
                    print("作业状态: running...")
            elif state == "done":
                jsonl_url = jr_json["data"]["resultUrl"]["jsonUrl"]
                print(f"[诊断] 作业完成, jsonl_url: {jsonl_url}")
                break
            elif state == "failed":
                error_msg = jr_json["data"].get("errorMsg", "未知错误")
                print(f"作业失败: {error_msg}")
                return None
            else:
                print(f"[诊断] 作业状态未知: {state}, 完整响应: {json.dumps(jr_json, ensure_ascii=False)[:500]}")

        # ===== 下载 JSONL 结果 =====
        print(f"[诊断] 正在下载结果: {jsonl_url}")
        jsonl_response = requests.get(jsonl_url)
        jsonl_response.raise_for_status()
        print(f"[诊断] 结果下载完成, 内容长度: {len(jsonl_response.text)} 字符")

        lines = jsonl_response.text.strip().split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            line_obj = json.loads(line)
            result = line_obj.get("result")
            if result and "layoutParsingResults" in result:
                print(f"[诊断] ========== OCR流程成功完成 ==========")
                return result

        print("[诊断] JSONL 结果中未找到有效的 layoutParsingResults")
        return None

    except Exception as e:
        print(f"[诊断] 请求异常: {e}")
        traceback.print_exc()
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

# 异步消费者协程：从队列取文件路径 → 用线程池运行同步OCR → 存库
async def ocr_worker(queue, db_path, on_success_callback, token):
    
    while True:
        file_path = None
        try:
            file_path = await queue.get()
            print(f"检测到新截图，开始识别: {file_path}")
            
            # 在线程池中运行同步请求，避免阻塞事件循环
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
            else:
                print(f"识别失败或超时，跳过: {file_path}")
            
            queue.task_done()
        except asyncio.CancelledError:
            # 被取消时也要标记已完成，避免队列死锁
            if file_path is not None:
                try:
                    queue.task_done()
                except ValueError:
                    pass
            break
        except Exception as e:
            print(f"处理任务时发生错误: {e}")
            # 确保异常时也不会导致队列死锁
            if file_path is not None:
                try:
                    queue.task_done()
                except ValueError:
                    pass

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
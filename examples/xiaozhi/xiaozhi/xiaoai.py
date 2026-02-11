import argparse
import asyncio
import threading

import numpy as np
import open_xiaoai_server

from xiaozhi.event import EventManager
from xiaozhi.ref import get_speaker, set_xiaoai
from xiaozhi.services.audio.stream import GlobalStream
from xiaozhi.services.speaker import SpeakerManager
from xiaozhi.utils.base import json_decode

ASCII_BANNER = """
 __  __  ___  _____ _    
|  \/  |/ _ \|  ___/ \   
| |\/| | | | | |_ / _ \  
| |  | | |_| |  _/ ___ \ 
|_|  |_|\___/|_|/_/   \_\
      server MAC mini
v1.0.0 
                                                                                                                
"""


class XiaoAI:
    mode = "xiaoai"
    speaker = SpeakerManager()
    async_loop: asyncio.AbstractEventLoop = None
    emotion_buffer = bytearray()
    last_emotion_ts = 0.0
    last_emotion = ""

    @classmethod
    def setup_mode(cls):
        set_xiaoai(cls)
        parser = argparse.ArgumentParser(
            description="小爱音箱接入小智 AI | by: https://del.wang"
        )
        parser.add_argument(
            "--mode",
            type=str,
            choices=["xiaoai", "xiaozhi"],
            default="xiaoai",
            help="运行模式：【xiaoai】使用小爱音箱的输入输出音频（默认）、【xiaozhi】使用本地电脑的输入输出音频",
        )
        args = parser.parse_args()
        if args.mode == "xiaozhi":
            cls.mode = "xiaozhi"

    @classmethod
    def on_input_data(cls, data: bytes):
        audio_array = np.frombuffer(data, dtype=np.int16)
        GlobalStream.input(audio_array.tobytes())
        # 本地情绪分析（1秒窗口，0.5秒节流）
        try:
            import time
            from xiaozhi.ref import get_xiaozhi
            from xiaozhi.services.audio.ser import SER
            from config import APP_CONFIG
            # 追加到缓冲区
            cls.emotion_buffer.extend(audio_array.tobytes())
            sr = 16000
            window_bytes = sr * 2  # 1s of int16
            throttle = float(APP_CONFIG.get("SER", {}).get("THROTTLE_SECONDS", 0.5))
            now = time.time()
            if len(cls.emotion_buffer) >= window_bytes and (now - cls.last_emotion_ts) >= throttle:
                window = np.frombuffer(cls.emotion_buffer[-window_bytes:], dtype=np.int16).astype(np.float32) / 32768.0
                # 检查音量，静音时跳过情绪分析
                boost = float(APP_CONFIG.get("vad", {}).get("boost", 1))
                rms = float(np.sqrt(np.mean((window * boost)**2)))
   
                #rms = float(np.sqrt(np.mean(window**2)))
                if rms < 0.01:
                    cls.last_emotion_ts = now
                    keep_bytes = int(sr * 2 * 0.5)
                    if len(cls.emotion_buffer) > keep_bytes:
                        cls.emotion_buffer = cls.emotion_buffer[-keep_bytes:]
                    return
                # 先尝试 SER 模型
                emotion = SER.instance().predict(window)
                if emotion is None:
                    # 回退到启发式
                    rms = float(np.sqrt(np.mean(window**2)))
                    zero_cross = float(np.sum(window[:-1] * window[1:] < 0)) / len(window)
                    corr = np.correlate(window, window, mode="full")[len(window)-1:]
                    min_lag = int(sr / 300)
                    max_lag = int(sr / 80)
                    lag = min_lag + int(np.argmax(corr[min_lag:max_lag]))
                    pitch = sr / max(lag, 1)
                    if rms > 0.05 and zero_cross > 0.08:
                        emotion = "angry"
                    elif rms > 0.04 and pitch > 200:
                        emotion = "happy"
                    elif rms < 0.02 and pitch < 120:
                        emotion = "sad"
                    else:
                        emotion = "neutral"
                if emotion and emotion != cls.last_emotion:
                    print(f"🎭 情绪识别：{emotion}")
                zx = get_xiaozhi()
                if zx and emotion:
                    zx.schedule(lambda e=emotion: zx.set_emotion(e))
                    #zx.schedule(lambda: zx.set_emotion(emotion))
                cls.last_emotion_ts = now
                # 保留最近0.5秒，限制缓冲区
                keep_bytes = int(sr * 2 * 0.5)
                if len(cls.emotion_buffer) > keep_bytes:
                    cls.emotion_buffer = cls.emotion_buffer[-keep_bytes:]
        except Exception:
            pass

    @classmethod
    def on_output_data(cls, data: bytes):
        async def on_output_data_async(data: bytes):
            return await open_xiaoai_server.on_output_data(data)

        asyncio.run_coroutine_threadsafe(
            on_output_data_async(data),
            cls.async_loop,
        )

    @classmethod
    async def run_shell(cls, script: str, timeout: float = 10 * 1000):
        return await open_xiaoai_server.run_shell(script, timeout)

    @classmethod
    async def on_event(cls, event: str):
        event_json = json_decode(event) or {}
        event_data = event_json.get("data", {})
        event_type = event_json.get("event")

        if not event_json.get("event"):
            return

        if event_type == "instruction" and event_data.get("NewLine"):
            line = json_decode(event_data.get("NewLine"))
            if (
                line
                and line.get("header", {}).get("namespace") == "SpeechRecognizer"
                and line.get("header", {}).get("name") == "RecognizeResult"
            ):
                text = line.get("payload", {}).get("results")[0].get("text")
                if not text and not line.get("payload", {}).get("is_vad_begin"):
                    print("🔥 唤醒小爱")
                    EventManager.on_interrupt()
                elif text and line.get("payload", {}).get("is_final"):
                    print(f"🔥 收到指令: {text}")
                    await EventManager.wakeup(text, "xiaoai")
        elif event_type == "playing":
            get_speaker().status = event_data.lower()

    @classmethod
    def __init_background_event_loop(cls):
        def run_event_loop():
            cls.async_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(cls.async_loop)
            cls.async_loop.run_forever()

        thread = threading.Thread(target=run_event_loop, daemon=True)
        thread.start()

    @classmethod
    def __on_event(cls, event: str):
        asyncio.run_coroutine_threadsafe(
            cls.on_event(event),
            cls.async_loop,
        )

    @classmethod
    async def init_xiaoai(cls):
        GlobalStream.on_output_data = cls.on_output_data
        open_xiaoai_server.register_fn("on_input_data", cls.on_input_data)
        open_xiaoai_server.register_fn("on_event", cls.__on_event)
        cls.__init_background_event_loop()
        print(ASCII_BANNER)
        await open_xiaoai_server.start_server()

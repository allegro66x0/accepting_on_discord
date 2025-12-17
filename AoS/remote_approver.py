import discord
from discord.ext import tasks
import pyautogui
import asyncio
import cv2  # OpenCV for confidence checking (optional but recommended)
try:
    import pyscreeze
    # Make sure we can use confidence
    pyscreeze.USE_IMAGE_NOT_FOUND_EXCEPTION = True
except ImportError:
    pass
import os
from io import BytesIO

# --- 設定項目 ---
import json
import sys

# --- 設定読み込み ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')

if not os.path.exists(CONFIG_PATH):
    print(f"Error: Config file not found at {CONFIG_PATH}")
    print("Please rename 'config.json.sample' to 'config.json' and set your token.")
    sys.exit(1)

with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    config = json.load(f)

DISCORD_TOKEN = config['DISCORD_TOKEN']
CHANNEL_ID = config['CHANNEL_ID']

# 画像パスを絶対パスに変換
TARGET_IMAGES = [os.path.join(BASE_DIR, img) for img in config['TARGET_IMAGES']]

CONFIDENCE_LEVEL = config.get('CONFIDENCE_LEVEL', 0.6)
CHECK_INTERVAL = config.get('CHECK_INTERVAL', 5)
IS_SCANNING = False # 監視状態フラグ

# PyAutoGUIの設定
pyautogui.FAILSAFE = True  # マウスを左上に持っていくと強制停止

class ApprovalView(discord.ui.View):
    def __init__(self, location):
        super().__init__(timeout=None) # タイムアウトなし
        self.location = location # ボタンが見つかった座標を保持

    @discord.ui.button(label="承認 (Accept)", style=discord.ButtonStyle.green, emoji="✅")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("承認操作を実行します...", ephemeral=True)
        
        # PC側での操作実行
        try:
            # マウスをボタンの位置に移動してクリック
            # ※ coordinatesは (left, top, width, height) なので中心を計算
            x = self.location.left + (self.location.width / 2)
            y = self.location.top + (self.location.height / 2)
            
            pyautogui.click(x, y)
            
            # あるいはショートカットキーの場合（例: Cmd+Enter）
            # pyautogui.hotkey('command', 'enter') 

            await interaction.followup.send(f"✅ PCでクリック操作を実行しました。")
            
            # ボタンを無効化して更新
            button.disabled = True
            button.label = "承認済み"
            button.style = discord.ButtonStyle.grey
            await interaction.message.edit(view=self)

        except Exception as e:
            await interaction.followup.send(f"❌ 操作に失敗しました: {e}")

    @discord.ui.button(label="拒否 / 無視", style=discord.ButtonStyle.red, emoji="❌")
    async def deny_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("操作をキャンセルしました。", ephemeral=True)
        # ボタンを無効化
        button.disabled = True
        self.children[0].disabled = True # 承認ボタンも無効化
        await interaction.message.edit(view=self)

class BotClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.is_waiting_response = False # 二重通知防止用フラグ

    async def on_ready(self):
        print(f'Logged in as {self.user}')
        print(f'Monitoring screen every {CHECK_INTERVAL} seconds...', flush=True)
        print(f'Target images: {len(TARGET_IMAGES)} files', flush=True)
        print('Press Ctrl+C to stop.', flush=True)
        
        # Test connection
        channel = self.get_channel(CHANNEL_ID)
        if channel:
            try:
                await channel.send("🚀 Remote Approver Ready! Type `!start` to begin monitoring.")
                print("Startup message sent successfully.", flush=True)
            except Exception as e:
                print(f"FAILED to send startup message: {e}", flush=True)
        else:
            print(f"Could not find channel with ID: {CHANNEL_ID}", flush=True)

        self.monitor_screen.start()

    async def on_message(self, message):
        global IS_SCANNING
        if message.author == self.user:
            return

        if message.content == '!start':
            IS_SCANNING = True
            await message.channel.send("👀 監視を開始しました (Scanning Started)")
            print("Command received: !start -> Scanning START", flush=True)

        elif message.content == '!stop':
            IS_SCANNING = False
            await message.channel.send("zzz 監視を停止しました (Scanning Stopped)")
            print("Command received: !stop -> Scanning STOP", flush=True)

    @tasks.loop(seconds=CHECK_INTERVAL)
    async def monitor_screen(self):
        global IS_SCANNING
        if not IS_SCANNING:
            return
        
        print("Scanning screen...", flush=True) # Heartbeat log
        try:
            found_location = None
            
            # 登録された画像を順番にチェック
            for img_path in TARGET_IMAGES:
                if not os.path.exists(img_path):
                    # 画像ファイルがない場合はスキップして警告
                    print(f"Warning: Image file not found: {img_path}")
                    continue

                try:
                    # 画面上にターゲット画像があるか探す
                    # print(f"Checking {os.path.basename(img_path)}...", flush=True)
                    found_location = pyautogui.locateOnScreen(img_path, confidence=CONFIDENCE_LEVEL)
                    if found_location:
                        print(f"検知しました ({os.path.basename(img_path)}): {found_location}", flush=True)
                        break # 1つ見つかればOK
                except pyautogui.ImageNotFoundException:
                    # print(f"Not found: {os.path.basename(img_path)}", flush=True)
                    continue # 次の画像を試す
                except Exception as e:
                    print(f"Error checking {os.path.basename(img_path)}: {e}", flush=True)
                    continue

            if found_location:
                channel = self.get_channel(CHANNEL_ID)
                if channel:
                    # 全画面スクショを撮る
                    screenshot = pyautogui.screenshot()
                    
                    # メモリ上で画像バイナリに変換
                    with BytesIO() as image_binary:
                        screenshot.save(image_binary, 'PNG')
                        image_binary.seek(0)
                        
                        file = discord.File(fp=image_binary, filename='screen.png')
                        
                        # View (ボタン) を作成して送信
                        view = ApprovalView(found_location)
                        await channel.send(
                            content="⚠️ **Antigravityからの承認リクエストを検知しました**\n変更内容を確認し、許可する場合はボタンを押してください。", 
                            file=file, 
                            view=view
                        )
                        
                        # 連続検知を防ぐため、少し待機させる
                        print("Notification sent. Cooling down for 30s...", flush=True)
                        await asyncio.sleep(30)

        except Exception as e:
            print(f"Loop Error: {e}", flush=True)
            pass

if __name__ == '__main__':
    client = BotClient()
    try:
        client.run(DISCORD_TOKEN)
    except discord.errors.LoginFailure:
        print("エラー: Discord Tokenが不正です。スクリプト内の 'DISCORD_TOKEN' を確認してください。")
    except Exception as e:
        print(f"実行エラー: {e}")

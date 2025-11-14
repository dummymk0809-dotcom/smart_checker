import subprocess
import paramiko
import sys
import serial
import time
from datetime import datetime
import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# ====================================================================
# 1. 設定定義
# ====================================================================

if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

# 設定ファイル名とパスを定義: SCRIPT_DIR を使って絶対パスを指定
CONFIG_FILE = os.path.join(SCRIPT_DIR, 'config.json')

try:
    # スクリプトと同じディレクトリにあるconfig.jsonを読み込む
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
except FileNotFoundError:
    print(f"ERROR: Configuration file '{CONFIG_FILE}' not found. Please create it.")
    sys.exit(1)
except json.JSONDecodeError:
    print(f"ERROR: Configuration file '{CONFIG_FILE}' is invalid JSON.")
    sys.exit(1)

SERIAL_PORT = config.get("SERIAL_PORT", "/dev/ttyACM0")
NAS_CONFIG = config.get("NAS_CONFIG", {})

# ====================================================================
# 2. 関数定義
# ====================================================================

def run_ssh_command(host, user, command, key_path):
    """
    SSH経由でNAS上のコマンドを実行し、出力を返す。
    """
    try:
        # 鍵ファイルパスの展開
        # key_path = os.path.expanduser(key_path)
        print(f"keypath = {key_path}")
        
        # SSHクライアントの初期化
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # 秘密鍵を使用して接続
        client.connect(
            hostname=host,
            username=user,
            key_filename=key_path,
            timeout=10
        )
        
        # コマンド実行
        print(f"Executing: {command}")
        stdin, stdout, stderr = client.exec_command(command, timeout=30)
        
        output = stdout.read().decode('utf-8').strip()
        error = stderr.read().decode('utf-8').strip()
        
        client.close()
        
        if error:
            # エラー出力を返す（smartctlの非0リターンコードも含む）
            return f"ERROR: {error}\nOutput: {output}"
        
        return output

    except paramiko.AuthenticationException:
        return "SSH ERROR: Authentication failed. Check user and key path."
    except paramiko.SSHException as e:
        return f"SSH ERROR: Could not establish SSH connection. {e}"
    except Exception as e:
        return f"An unexpected error occurred during SSH: {e}"

def get_disk_health(nas_key, device_path):
    """
    特定のNAS上のディスクのSMARTステータスを取得し、健康レベルを返す。
    """
    nas = NAS_CONFIG.get(nas_key)
    print(f"nas={nas}")
    if not nas:
        print(f"ERROR: NAS configuration key '{nas_key}' not found in config.json.")
        return 3 # 危険レベル
        
    smartctl_command = f"sudo {nas['smartctl_path']} -H {device_path}"
    
    output = run_ssh_command(
        nas['host'],
        nas['user'],
        smartctl_command,
        nas['ssh_key_path']
    )
    
    print(f"\n--- SMART Output for {nas_key}:{device_path} ---\n{output}\n------------------------------------------")

    # SMART Health Statusの結果を解析
    if "PASSED" in output:
        return 1 # 正常 (Green)
    elif "FAIL" in output or "FAILED" in output:
        return 3 # 危険 (Red)
    elif "SMART support is: Unavailable" in output:
        print("WARNING: SMART support is unavailable. Cannot check health.")
        return 2 # 注意 (Yellow/Orange)
    else:
        # その他の警告やエラー（例：接続エラー、コマンドパス間違いなど）
        if output.startswith("SSH ERROR") or output.startswith("ERROR"):
            return 3 # 接続や実行エラーは危険として扱う
        return 2 # その他の不明な状態は注意 (Yellow/Orange)

def send_to_arduino(data):
    """
    シリアルポート経由でArduinoにデータを送信する。
    """
    # Arduinoがリセットされるのを防ぐため、ポートを閉じたまま待機
    time.sleep(2) 
    
    try:
        with serial.Serial(SERIAL_PORT, 9600, timeout=1) as ser:
            # 接続時にArduinoがリセットされるため、少し待機
            time.sleep(2) 
            
            print(f"Sending to Arduino: '{data}'")
            # データと改行コードを送信
            ser.write(data.encode('utf-8') + b'\n')
            
            # Arduinoからの応答を待つ（オプション）
            # time.sleep(0.1)
            # response = ser.readline().decode('utf-8').strip()
            # if response:
            #     print(f"Arduino response: {response}")

    except serial.SerialException as e:
        print(f"SERIAL ERROR: Could not open or communicate with port {SERIAL_PORT}. {e}")
        print("Ensure Arduino is connected and the correct port is specified in config.json.")
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected serial error occurred: {e}")
        sys.exit(1)

# ====================================================================
# 3. メイン処理
# ====================================================================

def main():
    # コマンドライン引数からテストレベルを取得 (例: python smart_monitor.py 3)
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        test_level = int(sys.argv[1])
        if 1 <= test_level <= 3 or test_level == 999:
            max_alert_level = test_level
            print(f"--- Running in TEST MODE (Level: {max_alert_level}) ---")
        else:
            print("Invalid test level. Use 1, 2, 3, or 999.")
            sys.exit(1)
    else:
        # 通常モード: 全NASの全ディスクをチェック
        max_alert_level = 1 # 初期値を正常レベル(1)として設定
        
        print("--- Running in PRODUCTION MODE (Checking all NAS drives) ---")
        
        for nas_key, nas in NAS_CONFIG.items():
            print(f"\nChecking NAS: {nas_key}")
            for device in nas.get('devices', []):
                
                # ディスクの健康状態を取得 (1=正常, 2=注意, 3=危険)
                current_level = get_disk_health(nas_key, device)
                print(f"Health check for {device} on {nas_key}: Level {current_level}")
                
                # 最も危険なレベルを保持
                if current_level > max_alert_level:
                    max_alert_level = current_level
                    
        print(f"\n--- Final Max Alert Level: {max_alert_level} ---")

    # 現在の日付を取得し、MMDD形式にフォーマット (例: 1109)
    current_date_mmdd = datetime.now().strftime("%m%d")
    
    # 送信データを作成 (レベルコード + '_' + 日付プレフィックス + MMDD)
    # 例: "1_D1109"
    data_to_send = f"{max_alert_level}_D{current_date_mmdd}"
    
    # Arduinoにデータを送信
    send_to_arduino(data_to_send)

if __name__ == "__main__":
    main()

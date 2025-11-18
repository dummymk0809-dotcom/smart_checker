# ====================================================================
# ライブラリインポート
# ====================================================================
import os
import sys
import json
import time # NAS休眠復帰待ちのためのtimeモジュールを追加
from datetime import datetime
import serial
import paramiko
import socket # socket.timeoutのためにインポート

# 実行スクリプトのディレクトリをシステムパスに追加（Cron対策）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

# ====================================================================
# 1. 設定定義
# ====================================================================

# 設定ファイル名とパスを定義: SCRIPT_DIR を使って絶対パスを指定
CONFIG_FILE = os.path.join(SCRIPT_DIR, 'config.json')

# 1. 設定ファイルの読み込み
# ====================================================================
try:
    # 絶対パスでconfig.jsonを読み込む
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
except FileNotFoundError:
    print(f"ERROR: Configuration file '{CONFIG_FILE}' not found. Please create it or check path.")
    sys.exit(1)
except json.JSONDecodeError:
    print(f"ERROR: Configuration file '{CONFIG_FILE}' is invalid JSON.")
    sys.exit(1)

SERIAL_PORT = config.get("SERIAL_PORT", "/dev/ttyACM0")
NAS_CONFIG = config.get("NAS_CONFIG", {})

if not NAS_CONFIG:
    print("ERROR: NAS_CONFIG is empty. Please configure NAS hosts in config.json.")
    # NAS設定がない場合はArduinoを消灯状態にする（設定がない=問題なし、として扱う）
    pass

# ====================================================================
# 2. ヘルパー関数
# ====================================================================

# SMART結果のヘルスレベル判定
def get_health_level(output):
    """
    smartctlの出力を解析し、NASのヘルスレベルを返す
    レベル1: PASSED (正常)
    レベル2: Warning/Self-assessment (注意)
    レベル3: FAIL/FAILED または エラー (危険)
    """
    output_upper = output.upper()

    # 1. SSH接続エラーをチェック (最優先でレベル3)
    if "SSH ERROR" in output_upper:
        return 3

    # 2. SMARTステータスをチェック
    if "FAILED" in output_upper or "FAIL" in output_upper:
        return 3  # 危険: SMARTテスト失敗

    # 3. 実行結果に何らかのエラーメッセージが含まれる場合
    if "ERROR" in output_upper:
        # sudoersのエラーなども含むため、基本的には危険レベル
        # ただし、'ERROR'の後に正常なPASSEDが続く可能性もあるため、PASSEDをチェック
        if "PASSED" in output_upper:
            return 1
        return 3

    # 4. PASSEDをチェック
    if "PASSED" in output_upper:
        return 1  # 正常

    # 5. その他の状態 (例: WARN, Self-assessment)
    if "WARNING" in output_upper or "SELF-ASSESSMENT" in output_upper:
        return 2  # 注意

    # どのキーワードも見つからなかった場合（予期せぬ出力）
    return 2 # 不明な状態は注意として扱う

# ====================================================================
# 3. Arduinoとの通信
# ====================================================================

def send_alert_to_arduino(alert_level, date_str):
    """
    シリアルポート経由でArduinoにアラートレベルと日付を送信
    フォーマット例: '3_D1115'
    """
    serial_connection = None
    try:
        # シリアル接続
        serial_connection = serial.Serial(SERIAL_PORT, 9600, timeout=1)
        time.sleep(2) # Arduinoの起動待ち

        # 送信するデータを作成 (例: '3_D1115')
        data_to_send = f"{alert_level}_D{date_str}\n"

        # 送信
        serial_connection.write(data_to_send.encode('utf-8'))
        print(f"Sent to Arduino: '{data_to_send.strip()}'")

    except serial.SerialException as e:
        print(f"ERROR: Could not connect to Arduino at {SERIAL_PORT}. {e}")
    except Exception as e:
        print(f"An unexpected error occurred during serial communication: {e}")
    finally:
        if serial_connection and serial_connection.is_open:
            serial_connection.close()

# ====================================================================
# 4. NASヘルパー関数
# ====================================================================

# SSH接続を試行し、コマンドを実行する関数
def execute_ssh_command(host, user, command, key_path, wait_before_connect=0):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    # NASが休眠中の場合、接続前にウェイトを入れる
    if wait_before_connect > 0:
        print(f"Waiting {wait_before_connect} seconds for NAS to wake up...")
        time.sleep(wait_before_connect)
        
    # 最大3回リトライ
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # 接続
            client.connect(
                hostname=host,
                username=user,
                key_filename=key_path,
                timeout=10,  # 接続タイムアウトを10秒に設定
                banner_timeout=30 # バナータイムアウトも長めに設定
            )

            # コマンド実行
            stdin, stdout, stderr = client.exec_command(command)
            output = stdout.read().decode('utf-8')
            error = stderr.read().decode('utf-8')
            
            # コマンドの実行エラーは警告として出力
            if error and "sudoers" not in error and "WARNING" not in error:
                print(f"Command execution warning (stderr): {error.strip()}")
                
            client.close()
            # stdoutとstderrを結合して返す
            return output + error 

        except paramiko.ssh_exception.NoValidConnectionsError as e:
            # 接続拒否などのエラー (ホストがダウンしている可能性)
            print(f"Attempt {attempt + 1}/{max_retries}: SSH ERROR: No valid connection (Host down or refused). {e}")
        except socket.timeout:
            # タイムアウト
            print(f"Attempt {attempt + 1}/{max_retries}: SSH ERROR: Connection timed out.")
        except Exception as e:
            # その他のparamikoエラーや接続失敗
            print(f"Attempt {attempt + 1}/{max_retries}: SSH ERROR: Could not establish SSH connection. {e}")
        finally:
            # 接続失敗した場合でも、必ずclientを閉じる
            if client:
                client.close()

        # リトライ間隔
        if attempt < max_retries - 1:
            time.sleep(5) # 5秒待機してからリトライ

    return "SSH ERROR: Could not establish SSH connection. Max retries exhausted"

# ====================================================================
# 5. メイン処理
# ====================================================================

def main():
    # コマンドライン引数チェック (テスト用)
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        test_level = int(sys.argv[1])
        print(f"--- Running in TEST MODE (Sending Level {test_level}) ---")
        date_str = datetime.now().strftime("%m%d")
        send_alert_to_arduino(test_level, date_str)
        return

    print("--- Running in PRODUCTION MODE (Checking all NAS drives) ---")

    # 現在の日付を取得 (MMDD形式)
    date_str = datetime.now().strftime("%m%d")

    # 各NASをチェック
    max_alert_level = 0
    for nas_name, nas_info in NAS_CONFIG.items():
        print(f"\nChecking NAS: {nas_name}")
        
        # 休眠からの復帰時間 (デフォルトは0秒)
        wait_time = nas_info.get("wakeup_wait_seconds", 0) 

        # 各デバイスをチェック
        for device in nas_info.get("devices", []):
            full_command = f"sudo {nas_info['smartctl_path']} -H {device}"
            
            # 実行
            output = execute_ssh_command(
                nas_info['host'], 
                nas_info['user'], 
                full_command, 
                nas_info['ssh_key_path'],
                wait_time # 待機時間をここで渡す
            )
            
            # NAS情報とSMART出力を表示
            print(f"--- SMART Output for {nas_name}:{device} ---")
            print(output)
            print("------------------------------------------")

            # ヘルスレベルを判定
            health_level = get_health_level(output)
            print(f"Health check for {device} on {nas_name}: Level {health_level}")

            # 最大アラートレベルを更新
            max_alert_level = max(max_alert_level, health_level)
            
            # 最初のNASチェック後にウェイト時間をリセット（2回目以降のデバイスチェックでは待機しないようにする）
            wait_time = 0

    # 最終的な最大アラートレベルをArduinoに送信
    print(f"\n--- Final Max Alert Level: {max_alert_level} ---")
    send_alert_to_arduino(max_alert_level, date_str)


if __name__ == "__main__":
    main()

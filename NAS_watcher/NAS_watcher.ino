// TM1637ライブラリが必要です。Arduino IDEのライブラリマネージャで「TM1637Display」を検索してインストールしてください。
#include <TM1637Display.h>

// ====================================================================
// RGB LED設定
// ====================================================================
const int PIN_RED = 9;
const int PIN_GREEN = 10;
const int PIN_BLUE = 11;

// アラートレベル定義
const int LEVEL_NORMAL = 1;  // 正常 (緑)
const int LEVEL_NOTICE = 2;  // 注意 (黄/橙)
const int LEVEL_ALERT = 3;   // 危険 (赤)
const int LEVEL_OFF = 999;   // 消灯

// ====================================================================
// TM1637 (7セグ) 設定
// ====================================================================
// モジュールとUNOのピン接続に合わせて変更してください。
#define CLK 2  // CLKピン (例: D2)
#define DIO 3  // DIOピン (例: D3)

TM1637Display display(CLK, DIO);

// グローバル変数
int currentAlertLevel = LEVEL_OFF;
String serialData = ""; // シリアル受信バッファ
String currentDateMMDD = "0000"; // 現在表示中の日付データ (MMDD)

// ====================================================================
// 関数定義
// ====================================================================

// RGB LEDの色を設定
void setLedColor(int r, int g, int b) {
  // コモンカソード (Common Cathode) の場合、数値が大きいほど明るい
  analogWrite(PIN_RED, r);
  analogWrite(PIN_GREEN, g);
  analogWrite(PIN_BLUE, b);
}

// アラートレベルに基づいてLEDを点灯
void updateAlertLed(int level) {
  // Common Cathode (コモンカソード) 用の設定: 数値は明るさに直結 (0=OFF, 255=MAX)
  
  // 輝度を抑えた設定 (MAX 8/255)
  const int MAX_BRIGHTNESS = 8;
  int r = 0;
  int g = 0;
  int b = 0;

  switch (level) {
    case LEVEL_NORMAL: // 正常 (緑) - 輝度を下げて目に優しく修正
      r = 0;
      g = MAX_BRIGHTNESS; // 緑を点灯
      b = 0;
      break;
    case LEVEL_NOTICE: // 注意 (黄/橙)
      r = 255;  // 赤は最大
      g = 100;  // 緑は中程度 (赤+緑で黄色/橙色)
      b = 0;
      break;
    case LEVEL_ALERT: // 危険 (赤)
      r = 255; // 赤を最大
      g = 0;
      b = 0;
      break;
    case LEVEL_OFF: // 消灯
    default:
      r = 0;
      g = 0;
      b = 0;
      break;
  }
  
  // 設定した値をセット
  setLedColor(r, g, b);
}

// 7セグに月日を表示 (例: 11月09日 -> 1109)
void displayDate(String mmdd) {
  if (mmdd.length() < 4) return;

  uint8_t dispData[4];

  // TM1637DisplayのencodeDigitを使用して各桁をエンコード
  dispData[0] = display.encodeDigit(mmdd.substring(0, 1).toInt());
  dispData[1] = display.encodeDigit(mmdd.substring(1, 2).toInt());
  dispData[2] = display.encodeDigit(mmdd.substring(2, 3).toInt());
  dispData[3] = display.encodeDigit(mmdd.substring(3, 4).toInt());
  
  // 2桁目と3桁目の間にコロン（ドット）を点灯させる処理はコメントアウト
  // dispData[1] |= 0x80; 

  display.setSegments(dispData);
}

// ====================================================================
// セットアップ
// ====================================================================
void setup() {
  // RGB LEDピンの初期化
  pinMode(PIN_RED, OUTPUT);
  pinMode(PIN_GREEN, OUTPUT);
  pinMode(PIN_BLUE, OUTPUT);
  
  // コモンカソードの場合: 0, 0, 0で全消灯
  setLedColor(0, 0, 0); 

  Serial.begin(9600); // Pythonとボーレートを合わせる

  // 7セグの設定
  display.setBrightness(0x0a); // 明るさ設定 (0x00 - 0x0f)
  display.clear(); // 7セグをクリア
  
  serialData.reserve(20); // バッファサイズを大きく確保
}

// ====================================================================
// メインループ
// ====================================================================
void loop() {
  // 1. シリアル通信の処理 (データ受信)
  if (Serial.available()) {
    char inChar = Serial.read();
    
    // 改行コード (\n) が来たらデータの終わり
    if (inChar == '\n') {
      
      // データ例: "1_D1109" または "999" または "OFF"
      int separatorIndex = serialData.indexOf('_');
      
      if (separatorIndex != -1) {
        // 統合データ (レベル_日付) の処理
        String levelString = serialData.substring(0, separatorIndex);
        String dateData = serialData.substring(separatorIndex + 1);
        
        // レベルの更新とLED点灯
        int level = levelString.toInt();
        currentAlertLevel = level;
        updateAlertLed(currentAlertLevel);
        
        // 日付の更新
        if (dateData.startsWith("D") || dateData.startsWith("d")) {
            currentDateMMDD = dateData.substring(1);
        }
        
        // 7セグにレベルを短時間表示
        display.showNumberDec(level, true, 4, 0); 
        delay(1500); // 1.5秒間レベルを表示
        
        // レベル表示後、日付表示に戻す
        displayDate(currentDateMMDD);
        
      } else if (serialData.startsWith("OFF") || serialData.startsWith("off") ) {
        // OFFコマンド
        display.clear();
        updateAlertLed(LEVEL_OFF);
      } else {
        // エラー処理または単独の数値レベル（テスト用）
        Serial.println("Received unexpected single level data after integration.");
      }

      serialData = ""; // 受信バッファをクリア

    } else if (inChar != '\r') {
      // 改行 (\r) 以外をバッファに追加
      serialData += inChar;
    }
  }

  // 2. 7セグの常時表示を維持 (シリアルデータがない場合は何もせず、最後に受信した日付を表示し続ける)
  delay(10); 
}
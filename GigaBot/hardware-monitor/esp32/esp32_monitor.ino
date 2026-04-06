/**
 * esp32_monitor.ino — Sketch principal para ESP32-S3
 *
 * Funções:
 *  - Leitura do sensor DHT11 (temperatura e humidade) — cada 60s
 *  - Polling de comandos pendentes na API REST — cada 5s
 *  - Execução de comandos ON/OFF nos pinos de LED e relé
 *  - Confirmação (ACK) de execução dos comandos
 *  - Heartbeat para o servidor — cada 30s
 *  - Relay de SMS: recebe do Arduino via Serial, envia para /api/sms/sync
 *  - Exibição de dados no LCD I2C (16x2)
 *
 * Dependências (instalar via Library Manager):
 *  - DHT sensor library (Adafruit)
 *  - Adafruit Unified Sensor
 *  - LiquidCrystal I2C (johnrickman)
 *  - ArduinoJson (Benoit Blanchon) — versão 6.x ou 7.x
 *  - WiFi (built-in ESP32)
 *  - HTTPClient (built-in ESP32)
 *
 * Autor: Projecto IRSS Grupo 6
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <DHT.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <ArduinoJson.h>

/* ═══════════════════════════════════════════════════════════════════════════
   CONFIGURAÇÃO — Alterar conforme o ambiente
   ═══════════════════════════════════════════════════════════════════════════ */

// Credenciais WiFi
const char* WIFI_SSID     = "SUA_REDE_WIFI";
const char* WIFI_PASSWORD = "SUA_SENHA_WIFI";

// URL base do servidor (sem barra no final)
const char* SERVER_BASE   = "http://192.168.1.100:5000/api";

// ID deste dispositivo no servidor (registar via POST /api/devices antes)
const int   DEVICE_ID     = 1;

// ── Pinos ──────────────────────────────────────────────────────────────────
#define PIN_DHT      4      // Pino de dados do DHT11
#define PIN_LED1     2      // LED 1 (ou relé 1)
#define PIN_LED2     15     // LED 2 (ou relé 2)
#define PIN_RELAY    16     // Relé principal
#define DHT_TYPE     DHT11  // Tipo do sensor

// ── LCD I2C ────────────────────────────────────────────────────────────────
#define LCD_ADDR  0x27  // Endereço I2C do LCD (usar 0x3F se não funcionar)
#define LCD_COLS  16
#define LCD_ROWS  2

// ── Serial para comunicação com Arduino Uno ────────────────────────────────
// Usar Serial2 (pinos GPIO17=TX2, GPIO18=RX2) para não conflitir com USB
#define SERIAL_ARDUINO  Serial2
#define SERIAL_BAUD     9600
#define ARDUINO_BAUD    9600

// ── Intervalos de tempo (milissegundos) ───────────────────────────────────
#define INTERVAL_SENSOR    60000   // 60 segundos
#define INTERVAL_COMMANDS   5000   //  5 segundos
#define INTERVAL_HEARTBEAT 30000   // 30 segundos
#define INTERVAL_SMS_POLL  15000   // 15 segundos — verificar SMS do Arduino
#define WIFI_RETRY_DELAY    5000   //  5 segundos entre tentativas WiFi

/* ═══════════════════════════════════════════════════════════════════════════
   OBJECTOS GLOBAIS
   ═══════════════════════════════════════════════════════════════════════════ */

DHT                dht(PIN_DHT, DHT_TYPE);
LiquidCrystal_I2C  lcd(LCD_ADDR, LCD_COLS, LCD_ROWS);
HTTPClient         http;

// Controlo de tempo (millis sem bloqueio)
unsigned long lastSensorTime    = 0;
unsigned long lastCommandTime   = 0;
unsigned long lastHeartbeatTime = 0;
unsigned long lastSMSPollTime   = 0;
unsigned long lastLCDUpdate     = 0;

// Estado actual dos actuadores
bool led1State   = false;
bool led2State   = false;
bool relayState  = false;

// Última leitura de sensores
float lastTemp = NAN;
float lastHum  = NAN;

/* ═══════════════════════════════════════════════════════════════════════════
   FUNÇÕES AUXILIARES
   ═══════════════════════════════════════════════════════════════════════════ */

/** Formata a URL completa da API */
String apiUrl(const String& path) {
  return String(SERVER_BASE) + path;
}

/** Faz um POST JSON para a API. Retorna o código HTTP ou -1 em erro. */
int httpPost(const String& path, const String& jsonBody) {
  String url = apiUrl(path);
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(8000);  // 8 segundos de timeout

  int code = http.POST(jsonBody);
  if (code < 0) {
    Serial.printf("[HTTP] POST %s — Erro: %s\n", path.c_str(), HTTPClient::errorToString(code).c_str());
  }
  http.end();
  return code;
}

/** Faz um GET para a API. Retorna o body da resposta ou String vazia em erro. */
String httpGet(const String& path) {
  String url = apiUrl(path);
  http.begin(url);
  http.setTimeout(8000);

  int code = http.GET();
  String body = "";

  if (code == 200) {
    body = http.getString();
  } else {
    Serial.printf("[HTTP] GET %s — Código: %d\n", path.c_str(), code);
  }
  http.end();
  return body;
}

/** Actualiza o LCD com 2 linhas de texto (máx. 16 caracteres cada) */
void lcdShow(const String& line1, const String& line2) {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print(line1.substring(0, LCD_COLS));
  lcd.setCursor(0, 1);
  lcd.print(line2.substring(0, LCD_COLS));
}

/* ═══════════════════════════════════════════════════════════════════════════
   CONEXÃO WiFi
   ═══════════════════════════════════════════════════════════════════════════ */

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;

  Serial.printf("[WiFi] A ligar a '%s'...\n", WIFI_SSID);
  lcdShow("WiFi: ligando...", WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int tentativas = 0;
  while (WiFi.status() != WL_CONNECTED && tentativas < 20) {
    delay(500);
    Serial.print(".");
    tentativas++;
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("[WiFi] Ligado! IP: %s\n", WiFi.localIP().toString().c_str());
    lcdShow("WiFi: OK", WiFi.localIP().toString());
    delay(1500);
  } else {
    Serial.println("[WiFi] Falha na conexao!");
    lcdShow("WiFi: FALHOU", "Tentando...");
    delay(WIFI_RETRY_DELAY);
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
   LEITURA DO SENSOR DHT11
   ═══════════════════════════════════════════════════════════════════════════ */

void sendSensorData() {
  float temp = dht.readTemperature();
  float hum  = dht.readHumidity();

  // Verificar se a leitura é válida
  if (isnan(temp) || isnan(hum)) {
    Serial.println("[DHT11] Falha na leitura — sensor desconectado?");
    lcdShow("DHT11: ERRO", "Sensor inativo");
    return;
  }

  lastTemp = temp;
  lastHum  = hum;

  Serial.printf("[DHT11] Temp: %.1f°C | Hum: %.1f%%\n", temp, hum);

  // Construir payload JSON
  StaticJsonDocument<128> doc;
  doc["temperature"] = round(temp * 10.0) / 10.0;
  doc["humidity"]    = round(hum  * 10.0) / 10.0;

  String body;
  serializeJson(doc, body);

  String path = "/devices/" + String(DEVICE_ID) + "/sensor";
  int code = httpPost(path, body);

  if (code == 201) {
    Serial.println("[Sensor] Leitura enviada ao servidor com sucesso");
  } else {
    Serial.printf("[Sensor] Erro ao enviar leitura (HTTP %d)\n", code);
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
   POLLING E EXECUÇÃO DE COMANDOS
   ═══════════════════════════════════════════════════════════════════════════ */

/** Aplica um comando ON/OFF num pino e actualiza o estado local */
void executeCommand(const String& target, const String& command) {
  int pino   = -1;
  bool* flag = nullptr;

  if      (target == "LED1")  { pino = PIN_LED1;  flag = &led1State;  }
  else if (target == "LED2")  { pino = PIN_LED2;  flag = &led2State;  }
  else if (target == "RELAY") { pino = PIN_RELAY; flag = &relayState; }
  else {
    Serial.printf("[CMD] Alvo desconhecido: %s\n", target.c_str());
    return;
  }

  bool novoEstado = (command == "ON");
  digitalWrite(pino, novoEstado ? HIGH : LOW);
  if (flag) *flag = novoEstado;

  Serial.printf("[CMD] %s -> %s (pino %d)\n", target.c_str(), command.c_str(), pino);
}

/** Faz polling dos comandos pendentes e executa cada um */
void pollAndExecuteCommands() {
  String path = "/devices/" + String(DEVICE_ID) + "/pending-commands";
  String response = httpGet(path);
  if (response.isEmpty()) return;

  // Parse do JSON
  DynamicJsonDocument doc(1024);
  DeserializationError err = deserializeJson(doc, response);
  if (err) {
    Serial.printf("[CMD] Erro ao parsear JSON: %s\n", err.c_str());
    return;
  }

  if (!doc["success"].as<bool>()) return;

  JsonArray commands = doc["data"].as<JsonArray>();
  if (commands.size() == 0) return;

  Serial.printf("[CMD] %d comando(s) pendente(s) recebido(s)\n", (int)commands.size());

  for (JsonObject cmd : commands) {
    int         cmdId   = cmd["id"].as<int>();
    String      target  = cmd["target"].as<String>();
    String      command = cmd["command"].as<String>();

    // Executar no hardware
    executeCommand(target, command);

    // Confirmar execução no servidor (ACK)
    String ackPath = "/devices/" + String(DEVICE_ID) + "/command-ack/" + String(cmdId);
    int code = httpPost(ackPath, "{}");

    if (code == 200) {
      Serial.printf("[CMD] ACK enviado para comando #%d\n", cmdId);
    } else {
      Serial.printf("[CMD] Falha no ACK do comando #%d (HTTP %d)\n", cmdId, code);
    }

    delay(100); // pequena pausa entre comandos
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
   HEARTBEAT
   ═══════════════════════════════════════════════════════════════════════════ */

void sendHeartbeat() {
  StaticJsonDocument<128> doc;
  doc["ip"] = WiFi.localIP().toString();

  // Mensagem para o LCD no servidor (informativa)
  if (!isnan(lastTemp)) {
    char lcdMsg[32];
    snprintf(lcdMsg, sizeof(lcdMsg), "T:%.1fC H:%.1f%%", lastTemp, lastHum);
    doc["lcd_message"] = lcdMsg;
  }

  String body;
  serializeJson(doc, body);

  String path = "/devices/" + String(DEVICE_ID) + "/heartbeat";
  int code = httpPost(path, body);

  if (code == 200) {
    Serial.println("[HB] Heartbeat enviado com sucesso");
  } else {
    Serial.printf("[HB] Falha no heartbeat (HTTP %d)\n", code);
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
   COMUNICAÇÃO COM ARDUINO UNO (SIM900 SMS)
   ═══════════════════════════════════════════════════════════════════════════ */

/**
 * Protocolo serial simples entre ESP32-S3 e Arduino Uno:
 *
 * Arduino → ESP32: Envia JSON com lista de SMS recebidos
 *   Formato: {"sms":[{"phone":"+258...","body":"texto","at":"2024-01-01T12:00:00"}]}
 *
 * ESP32 → Arduino: Envia comando para enviar SMS
 *   Formato: SEND:+258841234567:Texto da mensagem\n
 *
 * ESP32 → Arduino: Solicita lista de SMS
 *   Formato: GET_SMS\n
 */

/** Lê buffer do Arduino via Serial2 até newline ou timeout */
String readArduinoLine(unsigned long timeoutMs = 3000) {
  String line = "";
  unsigned long start = millis();

  while (millis() - start < timeoutMs) {
    if (SERIAL_ARDUINO.available()) {
      char c = SERIAL_ARDUINO.read();
      if (c == '\n') break;
      if (c != '\r') line += c;
    }
  }
  return line;
}

/** Solicita SMS ao Arduino e faz sync para o servidor */
void syncSMSFromArduino() {
  // Pedir ao Arduino os SMS guardados
  SERIAL_ARDUINO.println("GET_SMS");
  Serial.println("[SMS] Solicitando SMS ao Arduino...");

  String response = readArduinoLine(5000);
  if (response.isEmpty()) {
    Serial.println("[SMS] Sem resposta do Arduino");
    return;
  }

  // Parse JSON vindo do Arduino
  DynamicJsonDocument arduinoDoc(2048);
  DeserializationError err = deserializeJson(arduinoDoc, response);
  if (err) {
    Serial.printf("[SMS] Erro ao parsear resposta do Arduino: %s\n", err.c_str());
    return;
  }

  JsonArray smsList = arduinoDoc["sms"].as<JsonArray>();
  if (smsList.size() == 0) {
    Serial.println("[SMS] Nenhum SMS novo no Arduino");
    return;
  }

  Serial.printf("[SMS] %d SMS recebido(s) do Arduino\n", (int)smsList.size());

  // Construir payload para o servidor
  DynamicJsonDocument syncDoc(4096);
  syncDoc["device_id"] = DEVICE_ID;
  JsonArray messages = syncDoc.createNestedArray("messages");

  for (JsonObject sms : smsList) {
    JsonObject msg = messages.createNestedObject();
    msg["direction"]    = "IN";
    msg["phone"]        = sms["phone"].as<String>();
    msg["body"]         = sms["body"].as<String>();
    msg["received_at"]  = sms["at"].as<String>();
  }

  String body;
  serializeJson(syncDoc, body);

  int code = httpPost("/sms/sync", body);
  if (code == 201) {
    Serial.println("[SMS] SMS sincronizados com o servidor com sucesso");
    // Confirmar ao Arduino que pode limpar os SMS guardados
    SERIAL_ARDUINO.println("CLEAR_SMS");
  } else {
    Serial.printf("[SMS] Falha na sincronização (HTTP %d)\n", code);
  }
}

/** Verifica se há SMS pendentes de envio no servidor e envia via Arduino */
void pollAndSendSMS() {
  String path = "/sms/pending-send?device_id=" + String(DEVICE_ID);
  String response = httpGet(path);
  if (response.isEmpty()) return;

  DynamicJsonDocument doc(2048);
  DeserializationError err = deserializeJson(doc, response);
  if (err) return;

  if (!doc["success"].as<bool>()) return;

  JsonArray messages = doc["data"].as<JsonArray>();
  if (messages.size() == 0) return;

  Serial.printf("[SMS] %d SMS pendente(s) para envio\n", (int)messages.size());

  for (JsonObject msg : messages) {
    int    smsId = msg["id"].as<int>();
    String phone = msg["phone_number"].as<String>();
    String body  = msg["message_body"].as<String>();

    // Enviar comando para o Arduino (que tem o SIM900)
    // Formato: SEND:<numero>:<mensagem>\n
    String cmd = "SEND:" + phone + ":" + body;
    SERIAL_ARDUINO.println(cmd);
    Serial.printf("[SMS] Enviando para Arduino: %s\n", cmd.c_str());

    // Aguardar confirmação do Arduino
    String ack = readArduinoLine(8000);
    bool sucesso = ack.startsWith("OK");

    if (sucesso) {
      // Confirmar envio no servidor
      String ackPath = "/sms/" + String(smsId) + "/sent-ack";
      httpPost(ackPath, "{}");
      Serial.printf("[SMS] SMS #%d enviado com sucesso\n", smsId);
    } else {
      Serial.printf("[SMS] Falha no envio do SMS #%d — resposta Arduino: '%s'\n", smsId, ack.c_str());
    }

    delay(500); // pausa entre envios
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
   ACTUALIZAÇÃO DO LCD
   ═══════════════════════════════════════════════════════════════════════════ */

void updateLCD() {
  String linha1 = "WiFi: ";
  String linha2 = "";

  if (WiFi.status() == WL_CONNECTED) {
    linha1 += WiFi.localIP().toString();
    if (!isnan(lastTemp)) {
      // Alternar entre temperatura/humidade e IP
      static int lcdPage = 0;
      lcdPage = (lcdPage + 1) % 2;

      if (lcdPage == 0) {
        linha1 = "Temp: " + String(lastTemp, 1) + " C";
        linha2 = "Hum:  " + String(lastHum,  1) + " %";
      } else {
        linha1 = "IP: " + WiFi.localIP().toString();
        linha2 = "ESP32-S3 Online";
      }
    } else {
      linha2 = "DHT11: aguard.";
    }
  } else {
    linha1 += "DESCON.";
    linha2 = "Reconectando...";
  }

  lcdShow(linha1, linha2);
}

/* ═══════════════════════════════════════════════════════════════════════════
   SETUP
   ═══════════════════════════════════════════════════════════════════════════ */

void setup() {
  // Serial de debug (USB)
  Serial.begin(115200);
  delay(500);
  Serial.println("\n[BOOT] ESP32-S3 Hardware Monitor iniciando...");

  // Serial para comunicação com Arduino Uno
  SERIAL_ARDUINO.begin(ARDUINO_BAUD, SERIAL_8N1, 18, 17); // RX=18, TX=17
  Serial.println("[BOOT] Serial Arduino (UART2) iniciado");

  // Inicializar LCD I2C
  Wire.begin();
  lcd.init();
  lcd.backlight();
  lcdShow("ESP32-S3", "Iniciando...");
  Serial.println("[BOOT] LCD I2C iniciado");

  // Inicializar sensor DHT11
  dht.begin();
  Serial.println("[BOOT] DHT11 iniciado");

  // Configurar pinos de saída
  pinMode(PIN_LED1,  OUTPUT);
  pinMode(PIN_LED2,  OUTPUT);
  pinMode(PIN_RELAY, OUTPUT);
  digitalWrite(PIN_LED1,  LOW);
  digitalWrite(PIN_LED2,  LOW);
  digitalWrite(PIN_RELAY, LOW);
  Serial.println("[BOOT] Pinos configurados (LED1, LED2, RELAY)");

  // Conectar ao WiFi
  connectWiFi();

  // Enviar heartbeat inicial
  if (WiFi.status() == WL_CONNECTED) {
    sendHeartbeat();
    sendSensorData();
  }

  Serial.println("[BOOT] Setup concluido. Loop iniciando...");
  lcdShow("Sistema OK", "Monitor ativo");
  delay(1000);
}

/* ═══════════════════════════════════════════════════════════════════════════
   LOOP PRINCIPAL
   ═══════════════════════════════════════════════════════════════════════════ */

void loop() {
  unsigned long agora = millis();

  // ── Verificar e reconectar WiFi se necessário ────────────────────────────
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Conexao perdida — reconectando...");
    connectWiFi();
    return; // reiniciar o loop após reconexão
  }

  // ── Heartbeat — cada 30 segundos ─────────────────────────────────────────
  if (agora - lastHeartbeatTime >= INTERVAL_HEARTBEAT) {
    lastHeartbeatTime = agora;
    sendHeartbeat();
  }

  // ── Leitura do sensor DHT11 — cada 60 segundos ──────────────────────────
  if (agora - lastSensorTime >= INTERVAL_SENSOR) {
    lastSensorTime = agora;
    sendSensorData();
  }

  // ── Polling de comandos pendentes — cada 5 segundos ─────────────────────
  if (agora - lastCommandTime >= INTERVAL_COMMANDS) {
    lastCommandTime = agora;
    pollAndExecuteCommands();
  }

  // ── Sync SMS do Arduino — cada 15 segundos ───────────────────────────────
  if (agora - lastSMSPollTime >= INTERVAL_SMS_POLL) {
    lastSMSPollTime = agora;
    syncSMSFromArduino();
    pollAndSendSMS();
  }

  // ── Actualizar LCD — cada 5 segundos ─────────────────────────────────────
  if (agora - lastLCDUpdate >= 5000) {
    lastLCDUpdate = agora;
    updateLCD();
  }

  // Pequena pausa para não sobrecarregar o processador
  delay(50);
}

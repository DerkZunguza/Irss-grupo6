# Hardware Monitor — ESP32-S3 + Arduino Uno / SIM900

Sistema de controlo e monitoramento de hardware com backend Flask, frontend PWA e sketch Arduino.

## Estrutura do Projecto

```
hardware-monitor/
├── backend/
│   ├── app.py              ← Aplicação Flask (factory)
│   ├── models.py           ← Modelos SQLAlchemy (4 tabelas)
│   ├── routes/
│   │   ├── devices.py      ← GET/POST/DELETE /api/devices + heartbeat
│   │   ├── sensors.py      ← POST/GET /api/devices/<id>/sensor
│   │   ├── commands.py     ← Controlo LED/relé + polling + ACK
│   │   └── sms.py          ← Sync/send/pending SMS via SIM900
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── index.html          ← SPA — 4 secções: Dashboard/Sensores/SMS/Logs
│   ├── manifest.json       ← PWA manifest
│   ├── service-worker.js   ← Cache-first (assets) + Network-first (API)
│   ├── css/style.css       ← Tema escuro, mobile-first, sidebar desktop
│   └── js/app.js           ← Toda a lógica do frontend
├── esp32/
│   └── esp32_monitor.ino   ← Sketch completo do ESP32-S3
├── docker-compose.yml      ← PostgreSQL + API Flask + Nginx
├── nginx.conf              ← Proxy reverso frontend/API
└── .env.example            ← Variáveis de ambiente
```

## Início Rápido

### 1. Configurar variáveis de ambiente
```bash
cp .env.example .env
# Editar .env com as suas credenciais
```

### 2. Iniciar com Docker Compose
```bash
docker compose up -d --build
```

- **API REST:** http://localhost:5000
- **Frontend:** http://localhost:8080
- **Health check:** http://localhost:5000/health

### 3. Registar o ESP32 no sistema
```bash
curl -X POST http://localhost:5000/api/devices \
  -H "Content-Type: application/json" \
  -d '{"name": "ESP32-S3 Lab", "type": "ESP32-S3"}'
```
Guarda o `id` retornado — usar no sketch e no `DEVICE_ID`.

## Configuração do ESP32-S3

Editar as constantes no início do `esp32_monitor.ino`:

```cpp
const char* WIFI_SSID     = "SUA_REDE_WIFI";
const char* WIFI_PASSWORD = "SUA_SENHA_WIFI";
const char* SERVER_BASE   = "http://IP_DO_SERVIDOR:5000/api";
const int   DEVICE_ID     = 1;  // ID registado acima
```

### Pinos por defeito

| Componente | Pino GPIO |
|------------|-----------|
| DHT11      | 4         |
| LED1       | 2         |
| LED2       | 15        |
| RELAY      | 16        |
| LCD SDA    | 21 (I2C)  |
| LCD SCL    | 22 (I2C)  |
| Arduino TX | 17 (UART2)|
| Arduino RX | 18 (UART2)|

### Bibliotecas necessárias (Arduino IDE / PlatformIO)
- `DHT sensor library` (Adafruit)
- `Adafruit Unified Sensor`
- `LiquidCrystal I2C` (johnrickman)
- `ArduinoJson` (Benoit Blanchon) ≥ 6.x

## API Reference resumida

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| GET | `/api/devices` | Listar dispositivos |
| POST | `/api/devices` | Registar dispositivo |
| POST | `/api/devices/<id>/heartbeat` | Heartbeat do ESP32 |
| POST | `/api/devices/<id>/sensor` | Enviar leitura DHT11 |
| GET | `/api/devices/<id>/sensor/history` | Histórico de sensores |
| POST | `/api/devices/<id>/control` | Controlar LED/relé |
| GET | `/api/devices/<id>/pending-commands` | Polling de comandos |
| POST | `/api/devices/<id>/command-ack/<cmd_id>` | Confirmar comando |
| POST | `/api/sms/sync` | Sync batch de SMS do SIM900 |
| GET | `/api/sms` | Listar SMS |
| POST | `/api/sms/send` | Enfileirar SMS para envio |

## Protocolo Serial ESP32 ↔ Arduino

| Direcção | Mensagem | Descrição |
|----------|----------|-----------|
| ESP32 → Arduino | `GET_SMS\n` | Pedir lista de SMS recebidos |
| Arduino → ESP32 | `{"sms":[{"phone":"+258...","body":"...","at":"..."}]}\n` | Lista de SMS |
| ESP32 → Arduino | `SEND:+258841234567:Texto\n` | Enviar SMS via SIM900 |
| Arduino → ESP32 | `OK\n` ou `ERROR\n` | Confirmação de envio |
| ESP32 → Arduino | `CLEAR_SMS\n` | Limpar SMS do Arduino após sync |

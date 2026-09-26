#include <Arduino.h>
#include <HTTPClient.h>
#include <WiFi.h>

// iConnect access point. An empty password is valid for an open network.
constexpr char WIFI_SSID[] = "iConnect-WiFi";
constexpr char WIFI_PASSWORD[] = "";

// This must be plain text, not a Markdown link.
constexpr char SERVER_URL[] = "http://10.10.10.1/api/hardware/voltage/";

// Set this to the same value as ESP32_API_KEY in the Orange Pi .env file.
// Do not reuse DEVICE_API_KEY: that key is allowed to submit coin credits.
constexpr char ESP32_API_KEY[] = "REPLACE_WITH_SEPARATE_ESP32_API_KEY";

// ESP32-C3 GPIO3 is ADC1_CH3 and is not a strapping pin.
constexpr uint8_t ADC_PIN = 3;

// Divider wiring for a nominal 12 V battery:
// Battery+ -> R1 (120k) -> GPIO3 -> R2 (20k) -> GND
// Also connect a 0.1 uF capacitor from GPIO3 to GND and share grounds.
// Ratio 7:1 keeps up to about 17.5 V below the C3 ADC's ~2.5 V limit.
constexpr float R1_OHMS = 120000.0F;
constexpr float R2_OHMS = 20000.0F;
constexpr float VOLTAGE_DIVIDER_RATIO = (R1_OHMS + R2_OHMS) / R2_OHMS;

// Compare the reported value with a multimeter, then adjust this slightly.
// Example: meter 12.60 V / reported 12.30 V = 1.024 calibration factor.
constexpr float CALIBRATION_FACTOR = 1.000F;

constexpr uint8_t ADC_SAMPLES = 32;
constexpr uint32_t SEND_INTERVAL_MS = 10000;
constexpr uint32_t WIFI_RETRY_INTERVAL_MS = 10000;
constexpr uint32_t WIFI_CONNECT_TIMEOUT_MS = 15000;

uint32_t lastSendAt = 0;
uint32_t lastWiFiAttemptAt = 0;

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  lastWiFiAttemptAt = millis();
  Serial.printf("Connecting to %s", WIFI_SSID);
  WiFi.disconnect(false);

  if (WIFI_PASSWORD[0] == '\0') {
    WiFi.begin(WIFI_SSID);
  } else {
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  }

  const uint32_t startedAt = millis();
  while (WiFi.status() != WL_CONNECTED &&
         millis() - startedAt < WIFI_CONNECT_TIMEOUT_MS) {
    delay(500);
    Serial.print('.');
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\nWi-Fi connected. IP: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("\nWi-Fi connection timed out; will retry.");
  }
}

float readBatteryVoltage() {
  uint32_t totalMillivolts = 0;
  for (uint8_t sample = 0; sample < ADC_SAMPLES; ++sample) {
    totalMillivolts += analogReadMilliVolts(ADC_PIN);
    delay(2);
  }

  const float adcVolts =
      (totalMillivolts / static_cast<float>(ADC_SAMPLES)) / 1000.0F;
  return adcVolts * VOLTAGE_DIVIDER_RATIO * CALIBRATION_FACTOR;
}

void sendVoltage(float batteryVoltage) {
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(5000);
  http.setTimeout(5000);

  if (!http.begin(client, SERVER_URL)) {
    Serial.println("Could not open the voltage endpoint.");
    return;
  }

  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-ESP32-API-KEY", ESP32_API_KEY);

  const String payload =
      "{\"device\":\"ESP32-C3 Battery Monitor\",\"voltage\":" +
      String(batteryVoltage, 2) + "}";

  const int httpCode = http.POST(payload);
  if (httpCode > 0) {
    Serial.printf("Voltage sent: %.2f V (HTTP %d)\n", batteryVoltage, httpCode);
    if (httpCode != HTTP_CODE_OK) {
      Serial.println(http.getString());
    }
  } else {
    Serial.printf("Send failed: %s\n", http.errorToString(httpCode).c_str());
  }

  http.end();
}

void setup() {
  Serial.begin(115200);
  delay(500);

  analogReadResolution(12);
  analogSetPinAttenuation(ADC_PIN, ADC_11db);

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  connectWiFi();

  // Make the first successful reading immediate.
  lastSendAt = millis() - SEND_INTERVAL_MS;
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    if (millis() - lastWiFiAttemptAt >= WIFI_RETRY_INTERVAL_MS) {
      connectWiFi();
    }
    delay(50);
    return;
  }

  if (millis() - lastSendAt >= SEND_INTERVAL_MS) {
    lastSendAt = millis();
    sendVoltage(readBatteryVoltage());
  }

  delay(50);
}

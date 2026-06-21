/*
 * lora_transmitter.ino
 * =====================
 * Heltec WiFi LoRa 32 V2 firmware for HAB payload
 *
 * Role: Bridge between Raspberry Pi (UART) and LoRa RF link (SX1276)
 *
 * - Listens on Serial (UART0, USB) at 115200 baud for newline-terminated
 *   ASCII strings from the Raspberry Pi.
 * - On receipt, immediately re-transmits over LoRa at 915 MHz.
 * - Sends "OK\n" back to Pi on successful TX, "ERR\n" on failure.
 * - Displays last packet summary on the OLED display.
 *
 * Board:    Heltec WiFi LoRa 32 V2
 * Library:  Heltec ESP32 Dev-Boards library (install via Arduino Library Manager)
 *           Search: "Heltec ESP32 Dev-Boards" by Heltec Automation
 *
 * LoRa settings (match on ground receiver):
 *   Frequency : 915.0 MHz  (North America; change to 868.0 for EU)
 *   Bandwidth : 125 kHz
 *   SF        : 9   (compromise between range and data rate)
 *   CR        : 4/5
 *   TX Power  : 20 dBm (max for SX1276)
 *   Preamble  : 8 symbols
 */

#include "heltec.h"   // Heltec unified library: LoRa + OLED + LED

// ── LoRa parameters ────────────────────────────────────────────────────────────
#define LORA_BAND      915E6   // Hz — change to 868E6 for Europe
#define LORA_SF        9
#define LORA_BW        125E3
#define LORA_CR        5       // coding rate denominator (5 = 4/5)
#define LORA_PREAMBLE  8
#define LORA_TX_POWER  20      // dBm

// ── Serial baud rate (Pi ↔ ESP32) ────────────────────────────────────────────
#define UART_BAUD      115200

// ── OLED display ──────────────────────────────────────────────────────────────
#define OLED_LINE_HEIGHT 10
#define DISPLAY_WIDTH    128

// ── Globals ───────────────────────────────────────────────────────────────────
static uint32_t txCount    = 0;
static uint32_t errCount   = 0;
static String   lastPacket = "";


// ── Setup ─────────────────────────────────────────────────────────────────────
void setup() {
  // Heltec.begin(display, LoRa, serial, PABOOST, LORA_BAND)
  Heltec.begin(true, true, true, true, LORA_BAND);

  Serial.begin(UART_BAUD);

  // Configure LoRa modem
  LoRa.setSpreadingFactor(LORA_SF);
  LoRa.setSignalBandwidth(LORA_BW);
  LoRa.setCodingRate4(LORA_CR);
  LoRa.setPreambleLength(LORA_PREAMBLE);
  LoRa.setTxPower(LORA_TX_POWER);
  LoRa.enableCrc();

  // Splash screen
  Heltec.display->clear();
  Heltec.display->setFont(ArialMT_Plain_10);
  Heltec.display->drawString(0, 0,  "HAB LoRa TX");
  Heltec.display->drawString(0, 12, "915 MHz  SF9");
  Heltec.display->drawString(0, 24, "Waiting for Pi...");
  Heltec.display->display();

  delay(500);
}


// ── Main loop ─────────────────────────────────────────────────────────────────
void loop() {
  if (Serial.available()) {
    // Read until newline (Pi sends "<packet>\n")
    String packet = Serial.readStringUntil('\n');
    packet.trim();

    if (packet.length() == 0) return;

    lastPacket = packet;

    // ── Transmit over LoRa ────────────────────────────────────────────────
    LoRa.beginPacket();
    LoRa.print(packet);
    int result = LoRa.endPacket();   // blocking; returns 1 on success

    if (result == 1) {
      txCount++;
      Serial.println("OK");
      updateDisplay(packet, true);
    } else {
      errCount++;
      Serial.println("ERR");
      updateDisplay(packet, false);
    }
  }
}


// ── OLED update ───────────────────────────────────────────────────────────────
void updateDisplay(const String& packet, bool success) {
  Heltec.display->clear();
  Heltec.display->setFont(ArialMT_Plain_10);

  // Line 0: status bar
  String status = success ? "TX OK #" + String(txCount)
                          : "TX ERR #" + String(errCount);
  Heltec.display->drawString(0, 0, status);

  // Lines 1–4: first ~80 chars of packet (OLED is narrow)
  // Packet: HAB,id,utc,temp,pres,bmpalt,lat,lon,gpsalt,spd,sats,fix,muons,adc,win*CS
  // Parse key fields for a more readable display
  int commaPositions[4] = {-1, -1, -1, -1};
  int found = 0;
  for (int i = 0; i < (int)packet.length() && found < 4; i++) {
    if (packet.charAt(i) == ',') {
      commaPositions[found++] = i;
    }
  }

  // Extract: id (field 1), temp (field 3), bmp_alt (field 5), muons (field 11)
  // We'll just show the first 3 fields on line 2 and let the user read the rest
  String shortPkt = packet.substring(0, min((int)packet.length(), 42));
  Heltec.display->drawString(0, 12, shortPkt);

  // Line 3: lat/lon snippet
  String remainder = packet.substring(min((int)packet.length(), 42));
  Heltec.display->drawString(0, 24, remainder.substring(0, min((int)remainder.length(), 42)));

  // Line 4: TX / ERR counts
  Heltec.display->drawString(0, 44, "TX:" + String(txCount) + " ERR:" + String(errCount));

  Heltec.display->display();
}

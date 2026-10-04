// face-led: acende o LED integrado quando a validação facial é aprovada.
//
// Protocolo (texto, um comando por linha, 115200 baud) — ver docs/protocol.md:
//   placa liga      -> "READY face-led/1"
//   PING            -> "PONG face-led/1"
//   APPROVED        -> LED aceso 3 s           -> "ACK APPROVED"
//   REJECTED        -> 3 piscadas rápidas      -> "ACK REJECTED"
//   ERROR           -> 1 piscada longa         -> "ACK ERROR"
//   OFF             -> apaga                   -> "ACK OFF"
//   outro           -> "ERR UNKNOWN"; linha > 31 caracteres -> "ERR TOO_LONG"
//
// Sem delay(): o padrão do LED avança por millis(), então um comando novo
// substitui o anterior na hora e o LED sempre termina apagado.

const char VERSION[] = "face-led/1";
const unsigned long BAUD = 115200;

// Padrão = durações alternadas aceso/apagado, em ms, começando aceso.
const unsigned int PATTERN_APPROVED[] = {3000};
const unsigned int PATTERN_REJECTED[] = {150, 150, 150, 150, 150};
const unsigned int PATTERN_ERROR[] = {1000};

const unsigned int *pattern = nullptr;
byte patternLength = 0;
byte patternStep = 0;
unsigned long stepStartedAt = 0;

char line[32];
byte lineLength = 0;
bool lineOverflow = false;

void startPattern(const unsigned int *steps, byte length) {
  pattern = steps;
  patternLength = length;
  patternStep = 0;
  stepStartedAt = millis();
  digitalWrite(LED_BUILTIN, HIGH);
}

void stopPattern() {
  pattern = nullptr;
  patternLength = 0;
  digitalWrite(LED_BUILTIN, LOW);
}

void updatePattern() {
  if (pattern == nullptr) return;
  if (millis() - stepStartedAt < pattern[patternStep]) return;
  patternStep++;
  if (patternStep >= patternLength) {
    stopPattern();
    return;
  }
  stepStartedAt = millis();
  // Passos pares acendem, ímpares apagam.
  digitalWrite(LED_BUILTIN, patternStep % 2 == 0 ? HIGH : LOW);
}

void handleCommand(const char *command) {
  if (strcmp(command, "PING") == 0) {
    Serial.print(F("PONG "));
    Serial.println(VERSION);
  } else if (strcmp(command, "APPROVED") == 0) {
    startPattern(PATTERN_APPROVED, sizeof(PATTERN_APPROVED) / sizeof(PATTERN_APPROVED[0]));
    Serial.println(F("ACK APPROVED"));
  } else if (strcmp(command, "REJECTED") == 0) {
    startPattern(PATTERN_REJECTED, sizeof(PATTERN_REJECTED) / sizeof(PATTERN_REJECTED[0]));
    Serial.println(F("ACK REJECTED"));
  } else if (strcmp(command, "ERROR") == 0) {
    startPattern(PATTERN_ERROR, sizeof(PATTERN_ERROR) / sizeof(PATTERN_ERROR[0]));
    Serial.println(F("ACK ERROR"));
  } else if (strcmp(command, "OFF") == 0) {
    stopPattern();
    Serial.println(F("ACK OFF"));
  } else {
    Serial.println(F("ERR UNKNOWN"));
  }
}

void readSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      if (lineOverflow) {
        Serial.println(F("ERR TOO_LONG"));
      } else if (lineLength > 0) {
        line[lineLength] = '\0';
        handleCommand(line);
      }
      lineLength = 0;
      lineOverflow = false;
      continue;
    }
    if (lineLength < sizeof(line) - 1) {
      line[lineLength++] = c;
    } else {
      lineOverflow = true;
    }
  }
}

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);
  Serial.begin(BAUD);
  Serial.print(F("READY "));
  Serial.println(VERSION);
}

void loop() {
  readSerial();
  updatePattern();
}

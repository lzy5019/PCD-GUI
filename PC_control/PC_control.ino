#include <Arduino.h>
#include <IRremote.hpp>
#include <ctype.h>
#include <stdlib.h>
#include <string.h>

// CNC Shield V3 default stepper pin map for Arduino Uno.
#define X_STEP_PIN 2
#define X_DIR_PIN 5
#define Y_STEP_PIN 3
#define Y_DIR_PIN 6
#define Z_STEP_PIN 4
#define Z_DIR_PIN 7
#define ENABLE_PIN 8

// Keep the IR receiver away from the CNC Shield STEP/DIR/ENABLE pins.
#define IR_RECEIVE_PIN A4

// IR remote command codes.
#define X_FORWARD_KEY 0x46
#define X_BACKWARD_KEY 0x15
#define Y_FORWARD_KEY 0x44
#define Y_BACKWARD_KEY 0x43
#define Z_FORWARD_KEY 0x42
#define Z_BACKWARD_KEY 0x4A
#define STOP_ALL_KEY 0x40
#define KEY_CONTINUOUS 0x16
#define KEY_LONG_PRESS 0x19
#define KEY_SINGLE_STEP 0x0D
#define KEY_COARSE_STEP 0x0C

enum MotorMode {
  CONTINUOUS,
  LONG_PRESS,
  SINGLE_STEP,
  COARSE_STEP,
};

struct AxisControl {
  char name;
  uint8_t stepPin;
  uint8_t dirPin;
  int8_t continuousDir;
  long queuedSteps;
  unsigned long lastStepMicros;
};

AxisControl xAxis = {'X', X_STEP_PIN, X_DIR_PIN, 0, 0, 0};
AxisControl yAxis = {'Y', Y_STEP_PIN, Y_DIR_PIN, 0, 0, 0};
AxisControl zAxis = {'Z', Z_STEP_PIN, Z_DIR_PIN, 0, 0, 0};

MotorMode currentMode = CONTINUOUS;

unsigned int pulseIntervalMicros = 500;
unsigned int stepPulseWidthMicros = 4;
long singleStepCount = 1;
long coarseStepCount = 20;
unsigned long longPressTimeoutMs = 150;

uint8_t lastCommand = 0;
unsigned long lastReceiveTime = 0;

char serialBuffer[96];
uint8_t serialLength = 0;

void processIr();
void handleIrMovement(uint8_t code);
bool isMovementKey(uint8_t code);
void processSerialInput();
void processSerialLine(char *line);
void printHelp();
void printStatus();
void setModeFromToken(const char *token);
AxisControl *axisFromToken(const char *token);
int8_t dirFromToken(const char *token);
void runAxis(AxisControl &axis, int8_t dir);
void jogAxis(AxisControl &axis, long steps);
void serviceAxis(AxisControl &axis);
void stopAxis(AxisControl &axis);
void stopContinuousMotors();
void stopAllMotors();
const __FlashStringHelper *modeName(MotorMode mode);
void uppercaseInPlace(char *text);

void setup() {
  Serial.begin(115200);

  pinMode(X_STEP_PIN, OUTPUT);
  pinMode(X_DIR_PIN, OUTPUT);
  pinMode(Y_STEP_PIN, OUTPUT);
  pinMode(Y_DIR_PIN, OUTPUT);
  pinMode(Z_STEP_PIN, OUTPUT);
  pinMode(Z_DIR_PIN, OUTPUT);
  pinMode(ENABLE_PIN, OUTPUT);
  digitalWrite(ENABLE_PIN, LOW);

  IrReceiver.begin(IR_RECEIVE_PIN, ENABLE_LED_FEEDBACK);

  Serial.println(F("Stepper IR/USB controller ready"));
  printHelp();
}

void loop() {
  processIr();
  processSerialInput();

  if (currentMode == LONG_PRESS && millis() - lastReceiveTime > longPressTimeoutMs) {
    stopContinuousMotors();
  }

  serviceAxis(xAxis);
  serviceAxis(yAxis);
  serviceAxis(zAxis);
}

void processIr() {
  if (!IrReceiver.decode()) {
    return;
  }

  uint8_t code = IrReceiver.decodedIRData.command;
  lastCommand = code;
  lastReceiveTime = millis();

  Serial.print(F("IR 0x"));
  Serial.println(code, HEX);

  switch (code) {
    case KEY_CONTINUOUS:
      currentMode = CONTINUOUS;
      stopAllMotors();
      Serial.println(F("OK MODE CONTINUOUS"));
      break;
    case KEY_LONG_PRESS:
      currentMode = LONG_PRESS;
      stopAllMotors();
      Serial.println(F("OK MODE LONG_PRESS"));
      break;
    case KEY_SINGLE_STEP:
      currentMode = SINGLE_STEP;
      stopAllMotors();
      Serial.println(F("OK MODE SINGLE_STEP"));
      break;
    case KEY_COARSE_STEP:
      currentMode = COARSE_STEP;
      stopAllMotors();
      Serial.println(F("OK MODE COARSE_STEP"));
      break;
    default:
      if (isMovementKey(code)) {
        handleIrMovement(code);
      }
      break;
  }

  IrReceiver.resume();
}

void handleIrMovement(uint8_t code) {
  if (code == STOP_ALL_KEY) {
    stopAllMotors();
    Serial.println(F("OK STOP"));
    return;
  }

  AxisControl *axis = NULL;
  int8_t dir = 0;

  switch (code) {
    case X_FORWARD_KEY:
      axis = &xAxis;
      dir = 1;
      break;
    case X_BACKWARD_KEY:
      axis = &xAxis;
      dir = -1;
      break;
    case Y_FORWARD_KEY:
      axis = &yAxis;
      dir = 1;
      break;
    case Y_BACKWARD_KEY:
      axis = &yAxis;
      dir = -1;
      break;
    case Z_FORWARD_KEY:
      axis = &zAxis;
      dir = 1;
      break;
    case Z_BACKWARD_KEY:
      axis = &zAxis;
      dir = -1;
      break;
  }

  if (axis == NULL || dir == 0) {
    return;
  }

  if (currentMode == CONTINUOUS || currentMode == LONG_PRESS) {
    runAxis(*axis, dir);
  } else if (currentMode == SINGLE_STEP) {
    jogAxis(*axis, dir * singleStepCount);
  } else if (currentMode == COARSE_STEP) {
    jogAxis(*axis, dir * coarseStepCount);
  }
}

bool isMovementKey(uint8_t code) {
  return code == X_FORWARD_KEY ||
         code == X_BACKWARD_KEY ||
         code == Y_FORWARD_KEY ||
         code == Y_BACKWARD_KEY ||
         code == Z_FORWARD_KEY ||
         code == Z_BACKWARD_KEY ||
         code == STOP_ALL_KEY;
}

void processSerialInput() {
  while (Serial.available() > 0) {
    char ch = (char)Serial.read();

    if (ch == '\r' || ch == '\n') {
      if (serialLength > 0) {
        serialBuffer[serialLength] = '\0';
        processSerialLine(serialBuffer);
        serialLength = 0;
      }
      continue;
    }

    if (serialLength < sizeof(serialBuffer) - 1) {
      serialBuffer[serialLength++] = ch;
    } else {
      serialLength = 0;
      Serial.println(F("ERR LINE_TOO_LONG"));
    }
  }
}

void processSerialLine(char *line) {
  char *cmd = strtok(line, " \t");
  if (cmd == NULL) {
    return;
  }

  uppercaseInPlace(cmd);

  if (strcmp(cmd, "?") == 0 || strcmp(cmd, "STATUS") == 0) {
    printStatus();
    return;
  }

  if (strcmp(cmd, "HELP") == 0) {
    printHelp();
    return;
  }

  if (strcmp(cmd, "STOP") == 0) {
    char *axisToken = strtok(NULL, " \t");
    if (axisToken == NULL) {
      stopAllMotors();
      Serial.println(F("OK STOP"));
      return;
    }

    AxisControl *axis = axisFromToken(axisToken);
    if (axis == NULL) {
      Serial.println(F("ERR BAD_AXIS"));
      return;
    }

    stopAxis(*axis);
    Serial.println(F("OK STOP_AXIS"));
    return;
  }

  if (strcmp(cmd, "MODE") == 0) {
    char *modeToken = strtok(NULL, " \t");
    if (modeToken == NULL) {
      Serial.println(F("ERR MODE_REQUIRED"));
      return;
    }
    setModeFromToken(modeToken);
    return;
  }

  if (strcmp(cmd, "RUN") == 0) {
    char *axisToken = strtok(NULL, " \t");
    char *dirToken = strtok(NULL, " \t");
    AxisControl *axis = axisFromToken(axisToken);
    int8_t dir = dirFromToken(dirToken);

    if (axis == NULL || dirToken == NULL) {
      Serial.println(F("ERR USE_RUN_AXIS_DIR"));
      return;
    }

    runAxis(*axis, dir);
    Serial.println(F("OK RUN"));
    return;
  }

  if (strcmp(cmd, "JOG") == 0) {
    char *axisToken = strtok(NULL, " \t");
    char *stepsToken = strtok(NULL, " \t");
    AxisControl *axis = axisFromToken(axisToken);

    if (axis == NULL || stepsToken == NULL) {
      Serial.println(F("ERR USE_JOG_AXIS_STEPS"));
      return;
    }

    jogAxis(*axis, atol(stepsToken));
    Serial.println(F("OK JOG"));
    return;
  }

  if (strcmp(cmd, "ENABLE") == 0) {
    char *enableToken = strtok(NULL, " \t");
    if (enableToken == NULL) {
      Serial.println(F("ERR ENABLE_VALUE_REQUIRED"));
      return;
    }

    int enable = atoi(enableToken);
    digitalWrite(ENABLE_PIN, enable ? LOW : HIGH);
    Serial.println(enable ? F("OK ENABLED") : F("OK DISABLED"));
    return;
  }

  if (strcmp(cmd, "SET") == 0) {
    char *name = strtok(NULL, " \t");
    char *valueToken = strtok(NULL, " \t");
    if (name == NULL || valueToken == NULL) {
      Serial.println(F("ERR USE_SET_NAME_VALUE"));
      return;
    }

    uppercaseInPlace(name);
    long value = atol(valueToken);
    if (strcmp(name, "PULSE") == 0 && value >= 50 && value <= 20000) {
      pulseIntervalMicros = (unsigned int)value;
      Serial.println(F("OK SET PULSE"));
    } else if (strcmp(name, "SINGLE") == 0 && value >= 1 && value <= 10000) {
      singleStepCount = value;
      Serial.println(F("OK SET SINGLE"));
    } else if (strcmp(name, "COARSE") == 0 && value >= 1 && value <= 100000) {
      coarseStepCount = value;
      Serial.println(F("OK SET COARSE"));
    } else if (strcmp(name, "LONGTIMEOUT") == 0 && value >= 20 && value <= 2000) {
      longPressTimeoutMs = (unsigned long)value;
      Serial.println(F("OK SET LONGTIMEOUT"));
    } else {
      Serial.println(F("ERR BAD_SET"));
    }
    return;
  }

  Serial.println(F("ERR UNKNOWN_COMMAND"));
}

void printHelp() {
  Serial.println(F("Commands:"));
  Serial.println(F("  ? or STATUS"));
  Serial.println(F("  STOP [X|Y|Z]"));
  Serial.println(F("  MODE CONT|LONG|SINGLE|COARSE"));
  Serial.println(F("  RUN X|Y|Z 1|-1|0"));
  Serial.println(F("  JOG X|Y|Z signed_steps"));
  Serial.println(F("  ENABLE 1|0"));
  Serial.println(F("  SET PULSE us | SET SINGLE steps | SET COARSE steps | SET LONGTIMEOUT ms"));
}

void printStatus() {
  Serial.print(F("STATUS MODE="));
  Serial.print(modeName(currentMode));
  Serial.print(F(" Xrun="));
  Serial.print(xAxis.continuousDir);
  Serial.print(F(" Xq="));
  Serial.print(xAxis.queuedSteps);
  Serial.print(F(" Yrun="));
  Serial.print(yAxis.continuousDir);
  Serial.print(F(" Yq="));
  Serial.print(yAxis.queuedSteps);
  Serial.print(F(" Zrun="));
  Serial.print(zAxis.continuousDir);
  Serial.print(F(" Zq="));
  Serial.print(zAxis.queuedSteps);
  Serial.print(F(" PULSE="));
  Serial.println(pulseIntervalMicros);
}

void setModeFromToken(const char *token) {
  char modeToken[16];
  strncpy(modeToken, token, sizeof(modeToken) - 1);
  modeToken[sizeof(modeToken) - 1] = '\0';
  uppercaseInPlace(modeToken);

  if (strcmp(modeToken, "CONT") == 0 || strcmp(modeToken, "CONTINUOUS") == 0) {
    currentMode = CONTINUOUS;
  } else if (strcmp(modeToken, "LONG") == 0 || strcmp(modeToken, "LONG_PRESS") == 0) {
    currentMode = LONG_PRESS;
  } else if (strcmp(modeToken, "SINGLE") == 0 || strcmp(modeToken, "SINGLE_STEP") == 0) {
    currentMode = SINGLE_STEP;
  } else if (strcmp(modeToken, "COARSE") == 0 || strcmp(modeToken, "COARSE_STEP") == 0) {
    currentMode = COARSE_STEP;
  } else {
    Serial.println(F("ERR BAD_MODE"));
    return;
  }

  stopAllMotors();
  Serial.print(F("OK MODE "));
  Serial.println(modeName(currentMode));
}

AxisControl *axisFromToken(const char *token) {
  if (token == NULL) {
    return NULL;
  }

  char axis = (char)toupper(token[0]);
  if (axis == 'X') {
    return &xAxis;
  }
  if (axis == 'Y') {
    return &yAxis;
  }
  if (axis == 'Z') {
    return &zAxis;
  }
  return NULL;
}

int8_t dirFromToken(const char *token) {
  if (token == NULL) {
    return 0;
  }
  if (strcmp(token, "1") == 0 || strcmp(token, "+") == 0 || strcmp(token, "+1") == 0) {
    return 1;
  }
  if (strcmp(token, "-1") == 0 || strcmp(token, "-") == 0) {
    return -1;
  }
  return 0;
}

void runAxis(AxisControl &axis, int8_t dir) {
  axis.queuedSteps = 0;
  axis.continuousDir = dir;
}

void jogAxis(AxisControl &axis, long steps) {
  if (steps == 0) {
    return;
  }
  axis.continuousDir = 0;
  axis.queuedSteps += steps;
}

void serviceAxis(AxisControl &axis) {
  int8_t dir = axis.continuousDir;
  if (axis.queuedSteps != 0) {
    dir = axis.queuedSteps > 0 ? 1 : -1;
  }

  if (dir == 0) {
    return;
  }

  unsigned long now = micros();
  if ((unsigned long)(now - axis.lastStepMicros) < pulseIntervalMicros) {
    return;
  }

  digitalWrite(axis.dirPin, dir > 0 ? HIGH : LOW);
  digitalWrite(axis.stepPin, HIGH);
  delayMicroseconds(stepPulseWidthMicros);
  digitalWrite(axis.stepPin, LOW);
  axis.lastStepMicros = now;

  if (axis.queuedSteps > 0) {
    axis.queuedSteps--;
  } else if (axis.queuedSteps < 0) {
    axis.queuedSteps++;
  }
}

void stopAxis(AxisControl &axis) {
  axis.continuousDir = 0;
  axis.queuedSteps = 0;
}

void stopContinuousMotors() {
  xAxis.continuousDir = 0;
  yAxis.continuousDir = 0;
  zAxis.continuousDir = 0;
}

void stopAllMotors() {
  stopAxis(xAxis);
  stopAxis(yAxis);
  stopAxis(zAxis);
}

const __FlashStringHelper *modeName(MotorMode mode) {
  switch (mode) {
    case CONTINUOUS:
      return F("CONTINUOUS");
    case LONG_PRESS:
      return F("LONG_PRESS");
    case SINGLE_STEP:
      return F("SINGLE_STEP");
    case COARSE_STEP:
      return F("COARSE_STEP");
  }
  return F("UNKNOWN");
}

void uppercaseInPlace(char *text) {
  if (text == NULL) {
    return;
  }

  while (*text != '\0') {
    *text = (char)toupper(*text);
    text++;
  }
}

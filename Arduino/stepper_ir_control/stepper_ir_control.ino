#include <Arduino.h>
#include <IRremote.hpp>

// 步进电机引脚
#define X_STEP_PIN  2
#define X_DIR_PIN   5
#define Y_STEP_PIN  3
#define Y_DIR_PIN   6
#define Z_STEP_PIN  4
#define Z_DIR_PIN   7
#define ENABLE_PIN  8 
// 红外接收引脚
#define IR_RECEIVE_PIN  A4
// 电机控制红外编码
#define X_FORWARD_KEY   0x46
#define X_BACKWARD_KEY  0x15
#define Y_FORWARD_KEY   0x44
#define Y_BACKWARD_KEY  0x43
#define Z_FORWARD_KEY   0x42
#define Z_BACKWARD_KEY  0x4A
#define STOP_ALL_KEY    0x40
// 模式控制红外编码
#define KEY_CONTINUOUS  0x16
#define KEY_LONG_PRESS  0x19
#define KEY_SINGLE_STEP 0x0D
#define KEY_COARSE_STEP 0x0C
// 电机状态
enum MotorState { STOP, FORWARD, BACKWARD };
MotorState xState = STOP;
MotorState yState = STOP;
MotorState zState = STOP;

// 脉冲间隔（微秒）
const int pulseDelay = 500;
const int singleStepCount = 1;
const int coarseStepCount = 20;
const unsigned long longPressTimeout = 50; 

// 电机控制模式
enum MotorMode {
  CONTINUOUS,   // 按键按下持续移动
  LONG_PRESS,   // 长按模式
  SINGLE_STEP,  // 单步移动
  COARSE_STEP,  // 粗调模式
};
MotorMode currentMode = CONTINUOUS;  // 默认模式

// 保存最近命令和时间
uint8_t lastCommand = 0;
unsigned long lastReceiveTime = 0;

// 函数声明
void stepMotor(int stepPin, int dirPin, MotorState state);
void stepMotorSteps(int stepPin, int dirPin, MotorState state, int steps);
void stopAllMotors();
void handleMovementCommand(uint8_t code);
bool isMovementKey(uint8_t code);




void setup() {
  Serial.begin(115200);

  // 电机初始化
  pinMode(X_STEP_PIN, OUTPUT);
  pinMode(X_DIR_PIN, OUTPUT);
  pinMode(Y_STEP_PIN, OUTPUT);
  pinMode(Y_DIR_PIN, OUTPUT);
  pinMode(Z_STEP_PIN, OUTPUT);
  pinMode(Z_DIR_PIN, OUTPUT);
  pinMode(ENABLE_PIN, OUTPUT);
  digitalWrite(ENABLE_PIN, LOW); // 低电平使能

  // 初始化红外接收器
  IrReceiver.begin(IR_RECEIVE_PIN, ENABLE_LED_FEEDBACK);
  Serial.println(F("IR Receiver ready"));
}

void loop() {
  bool receivedNewIR = false;
  uint8_t code = 0;

  // 检测红外信号
  if (IrReceiver.decode()) {
    code = IrReceiver.decodedIRData.command;
    lastCommand = code;
    lastReceiveTime = millis();
    receivedNewIR = true;

    Serial.print(F("IR code: 0x"));
    Serial.println(code, HEX);

    // 模式切换
    switch (code) {
      case KEY_CONTINUOUS:
        currentMode = CONTINUOUS;
        stopAllMotors();
        Serial.println(F("Mode: CONTINUOUS"));
        break;

      case KEY_LONG_PRESS:
        currentMode = LONG_PRESS;
        stopAllMotors();
        Serial.println(F("Mode: LONG_PRESS"));
        break;

      case KEY_SINGLE_STEP:
        currentMode = SINGLE_STEP;
        stopAllMotors();
        Serial.println(F("Mode: SINGLE_STEP"));
        break;

      case KEY_COARSE_STEP:
        currentMode = COARSE_STEP;
        stopAllMotors();
        Serial.println(F("Mode: COARSE_STEP"));
        break;
    }

    IrReceiver.resume(); // 接收下一条信号
  }

  // 根据当前模式执行动作
  switch (currentMode) {
    case CONTINUOUS:
      // 收到一次方向命令后，持续运动，直到 STOP_ALL_KEY
      if (receivedNewIR) {
        handleMovementCommand(lastCommand);
      }

      stepMotor(X_STEP_PIN, X_DIR_PIN, xState);
      stepMotor(Y_STEP_PIN, Y_DIR_PIN, yState);
      stepMotor(Z_STEP_PIN, Z_DIR_PIN, zState);
      break;

    case LONG_PRESS:
      // 只有不断收到方向命令，才持续运动；超时自动停止
      if (receivedNewIR) {
        handleMovementCommand(lastCommand);
      }

      if (millis() - lastReceiveTime > longPressTimeout) {
        stopAllMotors();
      }

      stepMotor(X_STEP_PIN, X_DIR_PIN, xState);
      stepMotor(Y_STEP_PIN, Y_DIR_PIN, yState);
      stepMotor(Z_STEP_PIN, Z_DIR_PIN, zState);
      break;

    case SINGLE_STEP:
      // 每次收到按键，只走固定步数
      if (receivedNewIR && isMovementKey(lastCommand)) {
        switch (lastCommand) {
          case X_FORWARD_KEY:
            stepMotorSteps(X_STEP_PIN, X_DIR_PIN, FORWARD, singleStepCount);
            break;
          case X_BACKWARD_KEY:
            stepMotorSteps(X_STEP_PIN, X_DIR_PIN, BACKWARD, singleStepCount);
            break;
          case Y_FORWARD_KEY:
            stepMotorSteps(Y_STEP_PIN, Y_DIR_PIN, FORWARD, singleStepCount);
            break;
          case Y_BACKWARD_KEY:
            stepMotorSteps(Y_STEP_PIN, Y_DIR_PIN, BACKWARD, singleStepCount);
            break;
          case Z_FORWARD_KEY:
            stepMotorSteps(Z_STEP_PIN, Z_DIR_PIN, FORWARD, singleStepCount);
            break;
          case Z_BACKWARD_KEY:
            stepMotorSteps(Z_STEP_PIN, Z_DIR_PIN, BACKWARD, singleStepCount);
            break;
          case STOP_ALL_KEY:
            stopAllMotors();
            break;
        }
      }
      break;
    
    case COARSE_STEP:
      if (receivedNewIR && isMovementKey(lastCommand)) {
        switch (lastCommand) {
          case X_FORWARD_KEY:
            stepMotorSteps(X_STEP_PIN, X_DIR_PIN, FORWARD, coarseStepCount);
            break;
          case X_BACKWARD_KEY:
            stepMotorSteps(X_STEP_PIN, X_DIR_PIN, BACKWARD, coarseStepCount);
            break;
          case Y_FORWARD_KEY:
            stepMotorSteps(Y_STEP_PIN, Y_DIR_PIN, FORWARD, coarseStepCount);
            break;
          case Y_BACKWARD_KEY:
            stepMotorSteps(Y_STEP_PIN, Y_DIR_PIN, BACKWARD, coarseStepCount);
            break;
          case Z_FORWARD_KEY:
            stepMotorSteps(Z_STEP_PIN, Z_DIR_PIN, FORWARD, coarseStepCount);
            break;
          case Z_BACKWARD_KEY:
            stepMotorSteps(Z_STEP_PIN, Z_DIR_PIN, BACKWARD, coarseStepCount);
            break;
          case STOP_ALL_KEY:
            stopAllMotors();
            break;
        }
      }
      break;
  }
}

void handleMovementCommand(uint8_t code) {
  switch (code) {
    case X_FORWARD_KEY:
      xState = FORWARD;
      break;
    case X_BACKWARD_KEY:
      xState = BACKWARD;
      break;
    case Y_FORWARD_KEY:
      yState = FORWARD;
      break;
    case Y_BACKWARD_KEY:
      yState = BACKWARD;
      break;
    case Z_FORWARD_KEY:
      zState = FORWARD;
      break;
    case Z_BACKWARD_KEY:
      zState = BACKWARD;
      break;
    case STOP_ALL_KEY:
      stopAllMotors();
      break;
    default:
      break;
  }
}

bool isMovementKey(uint8_t code) {
  return (code == X_FORWARD_KEY ||
          code == X_BACKWARD_KEY ||
          code == Y_FORWARD_KEY ||
          code == Y_BACKWARD_KEY ||
          code == Z_FORWARD_KEY ||
          code == Z_BACKWARD_KEY ||
          code == STOP_ALL_KEY);
}

void stopAllMotors() {
  xState = STOP;
  yState = STOP;
  zState = STOP;
}

void stepMotor(int stepPin, int dirPin, MotorState state) {
  if (state == STOP) return;

  digitalWrite(dirPin, state == FORWARD ? HIGH : LOW);

  digitalWrite(stepPin, HIGH);
  delayMicroseconds(pulseDelay);
  digitalWrite(stepPin, LOW);
  delayMicroseconds(pulseDelay);
}

void stepMotorSteps(int stepPin, int dirPin, MotorState state, int steps) {
  if (state == STOP) return;

  digitalWrite(dirPin, state == FORWARD ? HIGH : LOW);

  for (int i = 0; i < steps; i++) {
    digitalWrite(stepPin, HIGH);
    delayMicroseconds(pulseDelay);
    digitalWrite(stepPin, LOW);
    delayMicroseconds(pulseDelay);
  }
}



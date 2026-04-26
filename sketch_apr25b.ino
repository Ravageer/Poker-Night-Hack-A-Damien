#include <Display_Controller_New.h>


#define CLK_PIN 2
#define DT_PIN 3
#define SW_PIN A0

#define GREEN_LED A1 
#define RED_LED A2   

volatile int encoderCount = 0;

volatile bool encoderMoved = false; 

int lastCLKState;
int currentCLKState;

unsigned long lastButtonPress = 0;
const int debounceDelay = 10;

int currentMoney = 1000; 

void setup() {
  exeSetup();
  Serial.begin(9600);
  Serial.setTimeout(10); 

  pinMode(CLK_PIN, INPUT);
  pinMode(DT_PIN, INPUT);
  pinMode(SW_PIN, INPUT_PULLUP);
  
  pinMode(GREEN_LED, OUTPUT);
  pinMode(RED_LED, OUTPUT);
  
  digitalWrite(GREEN_LED, LOW);
  digitalWrite(RED_LED, HIGH);

  lastCLKState = digitalRead(CLK_PIN);
  
  
  attachInterrupt(digitalPinToInterrupt(CLK_PIN), updateEncoder, CHANGE);
  attachInterrupt(digitalPinToInterrupt(DT_PIN), updateEncoder, CHANGE);
}

void loop() {
  writeNum(currentMoney, 50); 

  
  if (encoderMoved) {
    Serial.print("POS:");
    Serial.println(encoderCount);
    encoderMoved = false; 
  }

  
  if (digitalRead(SW_PIN) == LOW) {
    if (millis() - lastButtonPress > debounceDelay) {
      Serial.println("CLICK");
      lastButtonPress = millis();
    }
  }

  
  if (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    command.trim();

    if (command == "TURN:1") {
      digitalWrite(RED_LED, LOW);
      digitalWrite(GREEN_LED, HIGH);
    } 
    else if (command == "TURN:0") {
      digitalWrite(GREEN_LED, LOW);
      digitalWrite(RED_LED, HIGH);
    }
    else if (command.startsWith("DISP:")) {
      String valueStr = command.substring(5);
      currentMoney = valueStr.toInt();
    }
  }
}


void updateEncoder() {
  currentCLKState = digitalRead(CLK_PIN);
  
  if (currentCLKState != lastCLKState && currentCLKState == 1) {
    if (digitalRead(DT_PIN) != currentCLKState) {
      encoderCount--; 
    } else {
      encoderCount++; 
    }
    
    
    encoderMoved = true;
  }
  lastCLKState = currentCLKState;
}
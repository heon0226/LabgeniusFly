
# For emulating
import threading
import time

import datetime

import logging
import zmq
# import smbus

import os
import numpy as np
from enum import IntEnum
import json

from api import util
from UserDefs import State, Command, Action

class State(IntEnum):
    READY = 0x00,
    RUNNING = 0x01,

class Command(IntEnum):
    READY = 0x00,
    PCR_RUN = 0x01,
    PCR_STOP = 0x02,
    FAN_ON = 0x03,
    FAN_OFF = 0x04,
    MAGNETO = 0x05

class Protocol():
    def __init__(self, label, temp, time):
        self.label = label
        self.temp = temp
        self.time = time

    def toDict(self):
        return {"label" : self.label, "temp" : self.temp, "time" : self.time}

# logger
logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Setting the file logger for PCR task
fileHandler = logging.FileHandler("pcr.log")
logger.addHandler(fileHandler)

logger.info("Logger started!")

# zmq setting
PCR_PORT = os.environ.get('PCR_PORT', '7001')
MAGNETO_PORT = os.environ.get('MAGNETO_PORT', '7005')

# test values
MagnetoState = ["Washing 1...", "Washing 2...", "Elution...", "Lysis...", "Mixing..."]
# Testphotodiode = [[392,392,386,380,379,379,377,377,375,374,373,373,371,370,369,362,365,367,365,367,370,367,370,367,374,383,392,423,474,559,673,803,933,1056,1161,1256,1338,1403,1454,1506],
#                   [492,492,486,480,479,479,477,477,475,474,473,473,471,470,469,462,465,467,465,467,470,467,470,467,474,483,492,523,574,659,773,903,1033,1156,1261,1356,1438,1503,1554,1606],
#                   [592,592,586,580,579,579,577,577,575,574,573,573,571,570,569,562,565,567,565,567,570,567,570,567,574,583,592,623,674,759,873,1003,1133,1256,1361,1456,1538,1603,1654,1706],
#                   [692,692,686,680,679,679,677,677,675,674,673,673,671,670,669,662,665,667,665,667,670,667,670,667,674,683,692,723,774,859,973,1103,1233,1356,1461,1556,1638,1703,1754,1806]]

# singletone pattern
class TaskWorker(threading.Thread):
    __instance = None
    
    @classmethod
    def _getInstance(cls):
        return cls.__instance

    @classmethod
    def instance(cls, *args, **kargs):
        cls.__instance = cls(*args, **kargs)
        cls.instance = cls._getInstance
        return cls.__instance

    def __init__(self):
        threading.Thread.__init__(self)
        # Daemon is important
        self.daemon = True

        # PCR ZMQ Socket initialize 
        self.context = zmq.Context()
        self.pcrClient = self.context.socket(zmq.REQ)
        self.pcrClient.connect('tcp://localhost:%s' % PCR_PORT)
        self.pcrMessage = { 'command' : 'none' }

        self.running = False
        self.currentCommand = Command.READY
        
        self.state = State.READY
        self.stateString = 'idle'

        self.currentTemp = 25.0
        self.remainingTotalSec = 0
        self.remainingGotoCount = 0
        self.currentActionNumber = 0
        self.totalActionNumber = 0
        self.completePCR = False
        self.photodiodes = [[], [], [], []]
        self.serialNumber = ''

        # for history
        self.result = ['', '', '', '']
        self.resultCts = ['', '', '', '']
		
        # Magneto Params
        self.magnetoIndex = 0
        self.magnetoCounter = 0
        self.magnetoRunning = False

        # Protocol
        self.protocol = []
        self.magnetoProtocol = []
        
        protocolData = util.getRecentProtocol()
        self.protocolName = protocolData[0]
        self.filters = protocolData[1]

        # For history
        self.result = ["", "", "", ""]
        self.resultCts = ["", "", "", ""]
        self.tempLogger = []
        for action in protocolData[2]:
            self.protocol.append(Action(**action))
        
        self.magnetoProtocol = protocolData[3]
        # self.magnetoProtocol = protocolData[5]
        
    # For getting the device status
    def run(self):
        roundTimer = time.time()
        while True:
            currentTime = time.time()
            if currentTime - roundTimer >= 0.5: # 500ms timer
                roundTimer = time.time()

                if self.currentCommand == Command.MAGNETO:
                    self.stateString = MagnetoState[self.magnetoIndex]
                    self.magnetoCounter += 1

                    if self.magnetoCounter == 3:
                        self.magnetoCounter = 0
                        self.magnetoIndex += 1

                        if self.magnetoIndex == len(MagnetoState):
                            self.running = True
                            self.magnetoRunning = False

                            self.startPCR()
                
                # Update Status
                self.pcrClient.send_json({})
                resp = self.pcrClient.recv_json()
                
                self.state = resp['state']
                self.running = resp['running']
                self.currentTemp = resp['temperature']
                self.remainingTotalSec = resp['remainingTotalSec']
                self.remainingGotoCount = resp['remainingGotoCount']
                self.currentActionNumber = resp['currentActionNumber']
                self.totalActionNumber = resp['totalActionNumber']
                self.completePCR = resp['completePCR']
                self.photodiodes = resp['photodiodes']
                self.serialNumber = resp['serialNumber']

                if self.state == State.RUNNING:
                    self.stateString = 'PCR in progress'
                
                # For History
                if self.running:
                    self.tempLogger.append(self.currentTemp)

                if self.completePCR:
                    self.processCleanupPCR()

            
    def initValues(self):
        self.running = False
        self.currentCommand = Command.READY
        
        self.state = State.READY
        self.stateString = 'idle'
        self.serialNumber = ''

        # Magneto Params
        self.magnetoIndex = 0
        self.magnetoCounter = 0
        self.magnetoRunning = False

        # Protocol
        self.protocol = []
        self.magnetoProtocol = []
        
        protocolData = util.getRecentProtocol()
        self.protocolName = protocolData[0]
        self.filters = protocolData[1]

        # For history
        self.result = ["", "", "", ""]
        self.resultCts = ["", "", "", ""]
        self.tempLogger = []
        for action in protocolData[2]:
            self.protocol.append(Action(**action))
        
        self.magnetoProtocol = protocolData[3]

    def startMagneto(self):
        pass
    
    def stopMagneto(self):
        pass

    def startPCR(self):
        # for history
        self.result = ['', '', '', '']
        self.resultCts = ['', '', '', '']

        self.pcrMessage['command'] = 'start'
        protocol = list(map(lambda x : x.__dict__, self.protocol))
        protocolData = [self.protocolName, self.filters, protocol]
        message = { 'command' : 'start', 'protocolData' : protocolData }

        print(protocolData)
        self.pcrClient.send_json(message)
        response = self.pcrClient.recv_json()

    def stopPCR(self):
        self.pcrClient.send_json({ 'command' : 'stop' })
        response = self.pcrClient.recv_json()
        logger.info('Stop Response', response)
        pass

    def getStatus(self):
        filters = []
        filterNames = []
        filterCts = []
        for key, val in self.filters.items():
            if val['use']:
                filters.append(key)
                filterNames.append(val['name'])
                filterCts.append(val['ct'])
            else:
                filters.append('')
                filterNames.append('')
                filterCts.append('')
        return {
            'running' : self.running,
            'command' : self.currentCommand,
            'temperature' : round(self.currentTemp, 2),
            'state' : self.state,
            'stateString' : self.stateString,
            "filters" : filters,
            "filterNames" : filterNames,
            "filterCts" : filterCts,
            'remainingTotalSec' : self.remainingTotalSec,
            'remainingGotoCount' : self.remainingGotoCount,
            'currentActionNumber' : self.currentActionNumber,
            'photodiodes' : self.photodiodes,
            'completePCR' : self.completePCR,
            'serialNumber' : self.serialNumber,
            'result' : self.result,
            'resultCts' : self.resultCts,
            'protocolName' : self.protocolName,
            'protocol' : [action.__dict__ for action in self.protocol],
            'magnetoProtocol' : self.magnetoProtocol
        }

    # internal protocol
    def reloadProtocol(self):
        if self.running or self.magnetoRunning:
            return False

        # reload the protocol
        self.initValues()
        return True

    def isRunning(self):
        return self.running or self.magnetoRunning

    def calcCT(self, index, filterCt):
        resultText = "FAIL"
        ct = 0
        sensorValue = np.array(self.photodiodes[index])
        idx = sensorValue.size
        if sensorValue.size > 10:
            # change calculate CT value function
            base_mean = sensorValue[3:16].mean()

            threshold = 0.697 * 4000.0 / 10
            logThreshold = np.log(threshold)
            logValue = np.log(sensorValue - base_mean)
            logValue[logValue <= 0] = 0

            for i in range(logValue.size):
                if logValue[i] > logThreshold:
                    idx = i
                    break

            if not 0 < idx < logValue.size:
                resultText = "Not detected"
            else:
                cpos = idx + 1
                cval = logValue[idx]
                delta = cval - logValue[idx-1]
                cq = cpos - (cval - logThreshold) / delta
                resultText = "Negative" if filterCt <= cq else "Positive"

    
        self.result[index] = resultText
        self.resultCts[index] = round(cq, 2)

    def processCleanupPCR(self):
        if self.state == State.READY:
            self.stateString = 'idle'
        filterData = []
        filterNames = []
        for index, key in enumerate(self.filters):
            if self.filters[key]['use']:
                filterData.append(key)
                filterNames.append(self.filters[key]['name'])

                self.calcCT(index, float(self.filters[key]['ct']))

        # save the history
        # need to change the timedelta for utc+9 now, when the internet connection is established, don't use this timedelta function.
        currentDate = (datetime.datetime.now() + datetime.timedelta(hours=9)).strftime('%Y-%m-%d %H:%M:%S')
        
        target = json.dumps(filterNames)
        filterData = json.dumps(filterData)
        ct = json.dumps(self.resultCts)

        result = json.dumps(self.result)
        graphData = json.dumps(self.photodiodes)
        tempData = json.dumps(self.tempLogger)
        logger.info("history saved!")

        util.saveHistory(currentDate, target, filterData, ct, result, graphData, tempData)
        # util.saveHistory(currentDate, target, filterData, ct, result, graphData, tempData)

        self.stopPCR()

#!/usr/bin/python
# -*- coding: utf-8 -*-

import logging
import socket
import pycurl
import time
import shlex
import subprocess
import paho.mqtt.client as mqtt

ALARM_DELAY = 5
URL_TEMPLATE = (
    "http://{host}:{port}/cgi-bin/eventManager.cgi?"
    "action=attach&codes=%5B{events}%5D"
  )
MQTT_BROKER_IP = ''
MQTT_BROKER_USERNAME = ''
MQTT_BROKER_PASSWORD = ''
CAMERAS = [{
    'host': '',
    'port': 80,
    'user': '',
    'pass': '',
    'events': (
                "VideoMotion,VideoBlind,VideoAbnormalDetection,SceneChange,"
                "CrossLineDetection,CrossRegionDetection,LeftDetection,"
                "SceneChange,TakenAwayDetection,FaceDetection,RioterDetection,"
                "MoveDetection,WanderDetection,CrossFenceDetection,"
                "ParkingDetection,NumberStat,RetrogradeDetection,"
                "TrafficJunction"
              )
    }]


class DahuaCamera:
    def __init__(
            self,
            master,
            index,
            camera,
    ):
        self.Master = master
        self.Index = index
        self.Camera = camera
        self.CurlObj = None
        self.Connected = None
        self.Reconnect = None

        self.Alarm = dict({'Active': None, 'Last': None})

    def SensorOn(self):
        sensorurl = 'home-assistant/cameras/{0}/IVS'.format(self.Index)
        client = mqtt.Client()
        client.connect(MQTT_BROKER_IP, 1883, 60)
        client.publish(sensorurl, 'ON')
        client.disconnect()

    def SensorOff(self):
        sensorurl = 'home-assistant/cameras/{0}/IVS'.format(self.Index)
        client = mqtt.Client()
        client.connect(MQTT_BROKER_IP, 1883, 60)
        client.publish(sensorurl, 'OFF')
        client.disconnect()

    def OnAlarm(self, State):
        if State:
            self.SensorOn()
            print ('Motion Detected')
        else:
            self.SensorOff()
            print ('Motion Stopped')

    def OnConnect(self):
        print ('[{0}] OnConnect()'.format(self.Index))
        self.Connected = True

    def OnDisconnect(self, reason):
        print ('[{0}] OnDisconnect({1})'.format(self.Index, reason))
        self.Connected = False

    def OnTimer(self):
        if self.Alarm['Active'] is False and time.time() \
                - self.Alarm['Last'] > ALARM_DELAY:
            self.Alarm['Active'] = None
            self.Alarm['Last'] = None

            self.OnAlarm(False)

    def OnReceive(self, data):
        Data = data.decode('utf-8', errors='ignore')

        for Line in Data.split('\r\n'):
            if Line == 'HTTP/1.1 200 OK':
                self.OnConnect()

            if not Line.startswith('Code='):
                continue

            Alarm = dict()
            for KeyValue in Line.split(';'):
                (Key, Value) = KeyValue.split('=')
                Alarm[Key] = Value

            self.ParseAlarm(Alarm)

    def ParseAlarm(self, Alarm):
        print ('[{0}] ParseAlarm({1})'.format(self.Index, Alarm))

        if Alarm['Code'] not in self.Camera['events'].split(','):
            return

        if Alarm['action'] == 'Start':
            if self.Alarm['Active'] is None:
                self.OnAlarm(True)
            self.Alarm['Active'] = True
        elif Alarm['action'] == 'Stop':
            self.Alarm['Active'] = False
            self.Alarm['Last'] = time.time()


class DahuaMaster:
    def __init__(self):
        self.Cameras = []
        self.NumActivePlayers = 0

        self.CurlMultiObj = pycurl.CurlMulti()
        self.NumCurlObjs = 0

        for (Index, Camera) in enumerate(CAMERAS):
            DahuaCam = DahuaCamera(self, Index, Camera)
            self.Cameras.append(DahuaCam)
            Url = URL_TEMPLATE.format(**Camera)

            CurlObj = pycurl.Curl()
            DahuaCam.CurlObj = CurlObj

            CurlObj.setopt(pycurl.URL, Url)
            CurlObj.setopt(pycurl.CONNECTTIMEOUT, 30)
            CurlObj.setopt(pycurl.TCP_KEEPALIVE, 1)
            CurlObj.setopt(pycurl.TCP_KEEPIDLE, 30)
            CurlObj.setopt(pycurl.TCP_KEEPINTVL, 15)
            CurlObj.setopt(pycurl.HTTPAUTH, pycurl.HTTPAUTH_DIGEST)
            CurlObj.setopt(pycurl.USERPWD, '%s:%s' % (Camera['user'],
                           Camera['pass']))
            CurlObj.setopt(pycurl.WRITEFUNCTION, DahuaCam.OnReceive)

            self.CurlMultiObj.add_handle(CurlObj)
            self.NumCurlObjs += 1

    def OnTimer(self):
        for Camera in self.Cameras:
            Camera.OnTimer()

    def Run(self, timeout=1.0):
        while 1:
            (Ret, NumHandles) = self.CurlMultiObj.perform()
            if Ret != pycurl.E_CALL_MULTI_PERFORM:
                break

        while 1:
            Ret = self.CurlMultiObj.select(timeout)
            if Ret == -1:
                self.OnTimer()
                continue

            while 1:
                (Ret, NumHandles) = self.CurlMultiObj.perform()

                if NumHandles != self.NumCurlObjs:
                    (_, Success, Error) = self.CurlMultiObj.info_read()

                    for CurlObj in Success:
                        Camera = next(
                            filter(
                                lambda x: x.CurlObj == CurlObj, self.Cameras
                            )
                        )
                        if Camera.Reconnect:
                            continue

                        Camera.OnDisconnect('Success')
                        Camera.Reconnect = time.time() + 5

                    for (CurlObj, ErrorNo, ErrorStr) in Error:
                        Camera = next(
                            filter(
                                lambda x: x.CurlObj == CurlObj, self.Cameras
                            )
                        )
                        if Camera.Reconnect:
                            continue

                        Camera.OnDisconnect(
                            '{0} ({1})'.format(ErrorStr, ErrorNo)
                        )
                        Camera.Reconnect = time.time() + 5

                    for Camera in self.Cameras:
                        if Camera.Reconnect and Camera.Reconnect \
                                < time.time():
                            self.CurlMultiObj.remove_handle(Camera.CurlObj)
                            self.CurlMultiObj.add_handle(Camera.CurlObj)
                            Camera.Reconnect = None

                if Ret != pycurl.E_CALL_MULTI_PERFORM:
                    break

            self.OnTimer()


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)

    Master = DahuaMaster()
    Master.Run()

import sys

print(sys.version)
print(sys.version_info)

import time
import clr
import os

clr.AddReference('C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.DeviceManagerCLI.dll')
clr.AddReference('C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.GenericMotorCLI.dll')
clr.AddReference('C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.KCube.DCServoCLI.dll')

from Thorlabs.MotionControl.DeviceManagerCLI import *
from Thorlabs.MotionControl.GenericMotorCLI import *
from Thorlabs.MotionControl.GenericMotorCLI import KCubeMotor
from Thorlabs.MotionControl.GenericMotorCLI.ControlParameters import JogParametersBase
from Thorlabs.MotionControl.KCube.DCServoCLI import *
from System import Decimal

from TLPMX import TLPMX
from ctypes import c_uint32, byref, create_string_buffer, c_bool, c_int, c_double

def main():
    '''The main entry point for the application'''
    os.add_dll_directory(os.getcwd())
    meter = TLPMX()
    device_count = c_uint32()
    meter.findRsrc(byref(device_count))

    if device_count == 0:
        print("No connected powermeter")
        quit()

    resource_name = create_string_buffer(1024)
    meter.getRsrcName(c_int(0), resource_name)

    meter.open(resource_name, c_bool(True), c_bool(True))
    meter.setWavelength(c_double(850.0), 1)


    serial_num = str('27273099')

    DeviceManagerCLI.BuildDeviceList()
    controller = KCubeDCServo.CreateKCubeDCServo(serial_num)

    if not controller == None:

        controller.Connect(serial_num)
        print("Connected to device: " + serial_num)
        
        if not controller.IsSettingsInitialized():
            controller.WaitForSettingsInitialized(3000)

        controller.StartPolling(50)
        time.sleep(0.1)
        controller.EnableDevice()
        time.sleep(0.1)

        config = controller.LoadMotorConfiguration(serial_num, DeviceConfiguration.DeviceSettingsUseOptionType.UseFileSettings)
        config.DeviceSettingsName = str('PRMI-Z8')
        config.UpdateCurrentConfiguration()
        controller.SetSettings(controller.MotorDeviceSettings, True, False) #Finalize settings, True to currently update device, False to not save to file

        #Home the device
        print("Device homing...")
        controller.Home(60000)

        #Set paramaters for jogging
        jog_params = controller.GetJogParams()
        jog_params.StepSize = Decimal(10)
        jog_params.MaxVelocity = Decimal(10)
        jog_params.JogMode = JogParametersBase.JogModes.SingleStep

        controller.SetJogParams(jog_params)

        print("Moving motor...")
        controller.MoveJog(MotorDirection.Forward, 0)
        time.sleep(0.25)

        #Read the powermeter value while motor is moving
        while controller.IsDeviceBusy:
            power = c_double()
            meter.measPower(byref(power), 1)
            print(f'{controller.Position}, {power.value*1000}')
            time.sleep(0.1)

        #Disconnect the motor
        controller.StopPolling()
        controller.Disconnect(False)
    #Close the powermeter
    meter.close()

if __name__ == "__main__":
    main()


#!/usr/bin/env python3
import threading
import netifaces as ni
import numpy as np
import math
import time
import serial
import socket
import signal
import logging
import fcntl
import struct
import threading as th
from gpiozero import AngularServo
servo = AngularServo(26, min_angle=-45, max_angle=45)

def TicTocGenerator():
    # Generator that returns time differences
    ti = 0           # initial time
    tf = time.time() # final time
    while True:
        ti = tf
        tf = time.time()
        yield tf-ti # returns the time difference

TicToc = TicTocGenerator() # create an instance of the TicTocGen generator

# This will be the main function through which we define both tic() and toc()
def toc(tempBool=True):
    # Prints the time difference yielded by generator instance TicToc
    tempTimeInterval = next(TicToc)
    if tempBool:
        print( "Elapsed time: %f seconds.\n" %tempTimeInterval )

def tic():
    # Records a time in TicToc, marks the beginning of a time interval
    toc(False)
                                   
fname = "newccsvwaypoints.txt"#"rtkwaypoints.txt"
L = 3 # meters
dstStream="192.168.162.50"#"24.99.125.134" #should be ip of laptop running on matlab
dstreamport=40000 #should be port of udp server running on matlab
logging.basicConfig(filename='app.log', filemode='w', format='%(message)s', level=logging.INFO)

##############
# PWM Declarations (PWM occurs on Arduino)#
##############
#General configs
maxTurnAngle=105.5 #in degrees
stopTurnAngle=90#74.5 #in degrees
minTurnAngle=74.5 #in degrees
ThrottleMin=0 #in Milliseconds
ThrottleStop=0 #in Milliseconds
ThrottleMax=0 #in Milliseconds
 
#default serial connection vals
turnAngle = stopTurnAngle
speed = ThrottleStop
nearStartingWaypoint = 0;
dataIn = 'null'
            
###############################
# Pure Pursuit Config#
###############################
# Mapping and localization
waypoints = []
waypoints_utm = []
                                              
# Pure Pursuit Variables

goalRadius = 3; # meters
spacingBetweenCoarseWaypoints =  0.05# 6 inches
pp_MaxTurnAngle = np.radians(14.5) #degrees to avoid too large a PWM value
MaxAngularVelocity = math.pi/8; # radians per second; (not implemented yet need to implement and optimize to reduce fast angular changes)

###############################
# Wi-Fi Hotspot Connection from Phone to RPi#
###############################

# UDP from phone
localPort_gps = 20001  # The RPi will open this port for receiving GPS and sensor input from phone.
localPort_mag = 20002  # The RPi will open this port for receiving GPS and sensor input from phone.
bufferSize = 1024

sensorDict = {}
sensorDict["compass"] = 90
###############################
# Serial connection for Arduino#
###############################

#Initialize serial connection
#ser = serial.Serial('/dev/ttyACM0', 9800, timeout=1)

#Set initial parameters on arduino
#def txSettings():
#    global stopTurnAngle
#    global ThrottleStop
    # output config legend: '<message_type(C for configure and D for controlls):initial_turn_angle:initial_speed>'
#    try:
#        message = 'C:' + str(int(stopTurnAngle)) + ':' + str(int(ThrottleStop))
#        ser.write(message.encode())
#    except:
#        print("ERROR: Could not send configs to Arduino")
#        return -1

#Send control data
#def txControls(): #Have one string fore setup and one for controls
#        global turnAngle
#        global speed
#        global nearStartingWaypoint
        #output string legend: '<message_type(C for configure and D for controlls):turn_angle:speed>'
#        try:
#            message = 'D:' + str(int(turnAngle)) + ':' + str(int(speed)) + ':' + str(int(nearStartingWaypoint))
#            ser.write(message.encode())
#        except:
#            print('ERROR: Could not send controls to Arduino')
#            return -1

#Run loop for sending and recieving serial data
#def serialLoop():
#    while True:
#        global dataIn
#        dat = ser.readline()
#        dataIn = dat.decode("utf-8").rstrip()
        #print("From Arduino: " + dataIn)
#        time.sleep(.05)

#set steering PWM value for arduino
#def turnControl(angle):
#    global turnAngle
#    turnAngle = 90 - angle

#set throttle PWM value for arduino
#def throttleControl(spdPercent):
#    global speed
#    speed = spdPercent
    ##calculate pwm value
    #pulseWidth = (pwmThrottleMax-pwmThrottleMin)*spdPercent/100+pwmThrottleMin
    #tempPwmSpeed = round((2**pwmBitRes)*(pulseWidth/(1/pwmBaseFrequency)))
    #if pwmSpeed != tempPwmSpeed:
        #pwmSpeed = tempPwmSpeed

######################
# Getting GPS and Sensor from Phone
######################
ni.ifaddresses('wlan0')
localIP = ni.ifaddresses('wlan0')[ni.AF_INET][0]['addr']
#localIP = get_ip_address('wlan0')#"192.168.101.203"#socket.gethostbyname(socket.gethostname())
# Create a datagram socket
UDPServerSocket_gps = socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM)
UDPServerSocket_gps.bind((localIP, localPort_gps))

UDPServerSocket_mag = socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM)
UDPServerSocket_mag.bind((localIP, localPort_mag))

def udpListener_gps(sensorDict):
    # Listen for incoming datagrams
    while True:
        bytesAddressPair = UDPServerSocket_gps.recvfrom(bufferSize)
        message = bytesAddressPair[0]
        sdata = message.decode('utf-8').split(',')
        sensorDict["gps"] = [float(sdata[1]),float(sdata[2])]
        if sdata[0] == 'None':
            g=1#sensorDict["compass"] = sensorDict["compass"]
        else:
            sensorDict["compass"] = float(sdata[0])
        time.sleep(.05)

def udpListener_mag2(sensorDict):
    c=0
    a=0
    while True:
        bytesAddressPair = UDPServerSocket_mag.recvfrom(bufferSize)
        message = bytesAddressPair[0]
        sdata=message.decode('utf-8').split(',')
        angle = math.atan2(float(sdata[1]), float(sdata[2]))
        angle = math.degrees(angle) + 270
        if angle > 360:
            angle = angle - 360
        sensorDict["compass"] = angle
        a+=angle
        if c==11:
            #sensorDict["compass"] = a/10
            c=0
            a=0;
        c+=1
        time.sleep(.05)


######################
# Pure Pursuit Controller
######################

def mysign(x):
    if x<0:
        return -1
    if x==0:
        return 0
    if x>0:
        return 1

def myrem(x,y):
    w = 0
    if x/y < 0:
        w = math.floor(x/y) + 1
    else:
        w = math.floor(x/y)
    return x - y * w

def purePursuit(pose, lx, ly, d):
    speedval = 1
    # local variables
    theta = pose[2]  # car heading relative to world x-axis (i.e., Magnetic East)
    beta = math.atan2((ly - pose[1]), (lx - pose[0]))  # direction in radians to goal point

    if abs(theta - beta) < .000001:
        gamma = 0
    else:
        gamma = theta - beta  # direction in radians to goal point in car's local coordinate where positive is right

    x_offset = d * math.sin(gamma) * -1
    y_offset = d * math.cos(gamma)
    turnangle = (2 * x_offset) / (d ** 2)

    thesign = mysign((math.sin(pose[2]) * (lx - pose[0])) - (math.cos(pose[2]) * (ly - pose[1])))
    turnangle = thesign * turnangle

    # Ensure the turn control saturates at MaxTurnAngle defined by servo
    if abs(turnangle) > pp_MaxTurnAngle:
        turnangle = thesign * pp_MaxTurnAngle

    turnangle = myrem(turnangle, 2 * math.pi)
    return turnangle, speedval


# deg2utm - converts GPS lat, lon (spherical coordinates) to utm (cartesian coordinates)
# The output is the x and y position of the T-Rover for a specific utmzone
def deg2utm(Lat, Lon):
    # Memory pre-allocation
    x = []
    y = []
    utmzone = []
    # Main Loop
    #
    la = Lat
    lo = Lon
    sa = 6378137.000000
    sb = 6356752.314245

    # e = ( ( ( sa ** 2 ) - ( sb ** 2 ) ) ** 0.5 ) / sa;
    e2 = (((sa ** 2) - (sb ** 2)) ** 0.5) / sb
    e2cuadrada = e2 ** 2
    c = (sa ** 2) / sb
    # alpha = ( sa - sb ) / sa;             #f
    # ablandamiento = 1 / alpha;   # 1/f
    lat = la * (math.pi / 180)
    lon = lo * (math.pi / 180)
    Huso = np.fix((lo / 6) + 31)
    S = ((Huso * 6) - 183)
    deltaS = lon - (S * (math.pi / 180))
    Letra = ''
    if (la < -72):
        Letra = 'C'
    elif (la < -64):
        Letra = 'D'
    elif (la < -56):
        Letra = 'E'
    elif (la < -48):
        Letra = 'F'
    elif (la < -40):
        Letra = 'G'
    elif (la < -32):
        Letra = 'H'
    elif (la < -24):
        Letra = 'J'
    elif (la < -16):
        Letra = 'K'
    elif (la < -8):
        Letra = 'L'
    elif (la < 0):
        Letra = 'M'
    elif (la < 8):
        Letra = 'N'
    elif (la < 16):
        Letra = 'P'
    elif (la < 24):
        Letra = 'Q'
    elif (la < 32):
        Letra = 'R'
    elif (la < 40):
        Letra = 'S'
    elif (la < 48):
        Letra = 'T'
    elif (la < 56):
        Letra = 'U'
    elif (la < 64):
        Letra = 'V'
    elif (la < 72):
        Letra = 'W'
    else:
        Letra = 'X'

    a = math.cos(lat) * math.sin(deltaS)
    epsilon = 0.5 * math.log((1 + a) / (1 - a))
    nu = math.atan(math.tan(lat) / math.cos(deltaS)) - lat
    v = (c / ((1 + (e2cuadrada * (math.cos(lat)) ** 2))) ** 0.5) * 0.9996
    ta = (e2cuadrada / 2) * epsilon ** 2 * (math.cos(lat)) ** 2
    a1 = math.sin(2 * lat)
    a2 = a1 * (math.cos(lat)) ** 2
    j2 = lat + (a1 / 2)
    j4 = ((3 * j2) + a2) / 4
    j6 = ((5 * j4) + (a2 * (math.cos(lat)) ** 2)) / 3
    alfa = (3 / 4) * e2cuadrada
    beta = (5 / 3) * alfa ** 2
    gama = (35 / 27) * alfa ** 3
    bm = 0.9996 * c * (lat - alfa * j2 + beta * j4 - gama * j6)
    xx = epsilon * v * (1 + (ta / 3)) + 500000
    yy = nu * v * (1 + ta) + bm
    if yy < 0:
        yy = 9999999 + yy
    x = xx
    y = yy
    utmzone = "%02d %c" % (Huso, Letra)
    return x, y, utmzone

def smoothWaypoints(wp_utm, spacing):
    la = wp_utm[0:,0]
    la = la.tolist()
    lo = wp_utm[0:,1]
    lo = lo.tolist()
    utmz = wp_utm[1,2]
    wla = [];
    wlo = [];
    u = [];
    for i in range(len(la) - 1):
        x2 = float(la[i + 1])
        y2 = float(lo[i + 1])
        x1 = float(la[i])
        y1 = float(lo[i])
        w1 = np.array([[x2], [y2]])
        wi = np.array([[x1], [y1]])
        v = w1 - wi;
        d = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2);
        num_points_that_fit = math.ceil(d / spacing);
        vd = (v / np.linalg.norm(v)) * spacing;
        for k in range(num_points_that_fit):
            wla.append((wi[0] + vd[0] * k));
            wlo.append((wi[1] + vd[1] * k));
            u.append(utmz);

    wla.append((float(la[len(la) - 1])))
    wlo.append((float(lo[len(lo) - 1])))
    u.append(utmz);
    return wla, wlo, u

def signal_handler(sig,frame):
    print('done\n')
    sys.exit(0)
           ######
# Main#
######
def main():
    c=1
    print('T-Rover Initializing...')

    print(' ')
    print('############START UDP###################')
    print('Setting Up UDP Servers...')
    # Set up thread for UDP Server (phone is pushing as client to RPI)
    th_gps_udp = threading.Thread(name='udpListener_gps', target=udpListener_gps, args=(sensorDict,))
    th_gps_udp.start()

    #th_mag_udp = threading.Thread(name='udpListener_mag2', target=udpListener_mag2, args=(sensorDict,))
    #th_mag_udp.start()

    print('Awaiting Valid GPS Signal...')

    # Check if sensorDict has gps value
    noGPS = True
    while noGPS:
        if 'gps' in sensorDict.keys():
            noGPS = False
        time.sleep(1)
    print('Valid GPS signal received from phone.')
    print('##############END UDP#################')
    print(' ')

    #print(' ')
    #print('#############START SERIAL##################')
    #global dataIn
    #th_arduino_serial = threading.Thread(name='serialLoop', target=serialLoop)
    #th_arduino_serial.start()
    #print('Awaiting: hello?, from Arduino...')
    #while True:
    #    if (dataIn == 'hello?'):
    #        break
    #    time.sleep(1)
    #print('Arduino communication established.')
    #print('Sending Config Settings to Arduino.')
    #txSettings()
    #while True:
    #    if (dataIn == 'thank you!'):
    #        break
    #   time.sleep(1)
    #print('Arduino configuration complete.')
    #print('#############END SERIAL##################')
    print(' ')
    print('##############START WAYPOINTS#################')
    print('Loading Coarse GPS Waypoints...')
    # read from local file "rtkwaypoints.txt" and read into 2-D float array called: waypoints.txt
    f3 = open(fname, "r")
    for x in f3:
        latLong = x.split(",");
        if ("\n" in latLong[1]):
            latLong[1] = latLong[1].replace("\n", "")
        latLong = [float(i) for i in latLong]
        waypoints.append(latLong)
    f3.close()

    print('Converting Coarse GPS Waypoints to UTM Coordinates')
    # convert all coarse gps waypoints.txt to utm coordinates
    for i in range(len(waypoints)):
        [txx, tyy, tuu] = deg2utm(waypoints[i][0], waypoints[i][1])
        waypoints_utm.append([txx, tyy, tuu])

    np_waypoints_utm = np.array(waypoints_utm)
    print('Smoothing UTM Waypoints...')
    # smooth coarse utm waypoints.txt
    [sxx, syy, suu] = smoothWaypoints(np_waypoints_utm, spacingBetweenCoarseWaypoints)
    troverGoal = (sxx[-1], syy[-1])
    troverGoal = np.array(troverGoal)
    print('##############END WAYPOINTS#################')
    print('')
    #print('##############START INIT POSITION#################')
    #print('Please place T-Rover near the first waypoint...')
    #global nearStartingWaypoint
    #while False:
    #    rover_lat = sensorDict["gps"][0]  # gps lat
    #    rover_lon = sensorDict["gps"][1]  # gps long
        #rover_lat = 33.830619
                                #rover_lon = -84.587818
    #    [rover_x, rover_y, utmzone] = deg2utm(rover_lat, rover_lon)
    #    theRange = range(len(sxx) - 1, -1, -1)
    #    goal_x = sxx[0]  # W[0]
    #    goal_y = syy[0]  # W[1]
    #    x2 = rover_x
    #    y2 = rover_y
    #    d = math.sqrt((goal_x - x2) ** 2 + (goal_y - y2) ** 2)
    #    if d <= L:
    #        nearStartingWaypoint = 1
    #        break
        #txControls()
    #    servo.angle = 0
    #    time.sleep(.1)
    #print('T-Rover is Near Initial Waypoint!')
    #print('##############END INIT POSITION#################')
    print('')
    print(' ')
    print('T-Rover System Ready!')
    print('T-Rover Pure Pursuit Begin!')
    distanceToGoal = 9999 # initial value
    utmzone = ''
    ss = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    o="%s"%(localIP)
    ss.sendto(o.encode(),(localIP,40001))
    while (distanceToGoal > goalRadius):
        #noPose = True
        #while noPose:
        #    if sensorDict["gps"][0] != 'null':
        #        noPose = False
        #    time.sleep(.1)
        rover_lat = sensorDict["gps"][0] #gps lat
        rover_lon = sensorDict["gps"][1] #gps long
        #test init gps 33.830619, -84.587818
        #rover_lat = 33.830619
        #rover_lon = -84.587818
        rover_heading_deg = sensorDict["compass"] # bearing angle (we may need to smooth this)

        #print(rover_heading_deg)
        rover_heading_rad = float(np.radians(rover_heading_deg))
        [rover_x, rover_y, utmzone] = deg2utm(rover_lat, rover_lon) # convert robot position from gps to utm
        pose = [rover_x, rover_y, rover_heading_rad]

        #print('Current pose: %f, %f, %f' % (pose[0],pose[1],pose[2]))
        pose = np.array(pose)
        #print('Current pose: %f, %f, %f' % (pose[0], pose[1], pose[2]))
        #print(pose)
        # Calculate distance to goal
        distanceToGoal = np.linalg.norm(pose[0:1] - troverGoal)
        #print('Distance to goal: %d' % (distanceToGoal))
        # Calculate goal point in utm coordinates
        for i in range(len(sxx)-1,-1,-1):
            #W = wp_utm_smooth[i]
            goal_x = sxx[i]#W[0]
            goal_y = syy[i]#W[1]
            x2 = pose[0]
            y2 = pose[1]
            d = math.sqrt((goal_x - x2)**2 + (goal_y - y2)**2)
            if d <= L:
                break
        #print('Goal_X: %d, Goal_Y: %d' % (goal_x, goal_y))
        [turnAngle_rad, speedValue] = purePursuit(pose, goal_x, goal_y, d)
        turnAngle_deg = float(np.degrees(turnAngle_rad))
        servo.angle = -turnAngle_deg
        logging.info("%s,%s,%s,%s,%s,%s,%s,%s" % (rover_lat, rover_lon,rover_heading_deg,goal_x,goal_y,turnAngle_deg,L,d))
        o="%s,%s,%s,%s,%s,%s,%s,%s\n" % (rover_lat, rover_lon,rover_heading_deg,goal_x,goal_y,turnAngle_deg,L,d)

        if (c%10)==0:
            c=0        
            print('Turn Angle (Deg): %f, D_Goal: %d' % (turnAngle_deg, d))
            ss.sendto(o.encode(), (dstStream, dstreamport))
                                              
        #turnControl(turnAngle_deg)
        #throttleControl(speedValue)

        c+=1
        #while True:o
        #    if dataIn.find("ack") != -1:
        #        xx=dataIn.split()
        #        print(xx[1])
        #        dataIn = '0'
        #        break
        #    time.sleep(.05)
    print('Goal Reached!')

signal.signal(signal.SIGINT,signal_handler)
main()

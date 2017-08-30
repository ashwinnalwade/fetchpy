#!/usr/bin/env python
import numpy as np
import rospy
import time
from openravepy import *
import openravepy
import fetchpy


class plottingPoints:
	def __init__(self, env, handles):
		self.env = env
		self.handles = handles

	def plotPoint(self, pose, size, color):
		point = np.array((pose[0:3]))
		self.handles.append(self.env.plot3(points = point, pointsize = size,colors = np.array((color)), drawstyle = 1))

	def plotPoints(self, poses, size, color):
		for i in poses:
			self.plotPoint(i, size, color)

def waitrobot(robot):
    while not robot.GetController().IsDone():
        time.sleep(0.05)

def createZigZagHalf(pose, length):
    poses = []
    app_pose = pose
    disc = np.sqrt(length)/10000.
    add_pos = [disc, disc, 0, 0,0,0]
    add_neg = [disc, -disc, 0, 0,0,0]
    for i in range(length):
        if(i<length/2):
            app_pose = app_pose+add_pos
        elif(i>=length/2):
            app_pose = app_pose+add_neg
        poses.append(app_pose)
    return poses

def createPattern(pose, length, num):
    poses = createZigZagHalf(pose, length)
    for i in range(num-1):
        add_poses = createZigZagHalf(poses[-1], length)
        for j in add_poses:
            poses.append(j)
    return poses

def getTransformBetweenLinks(link1, link2):
    trns1 = robot.GetLinkTransformations()[robot.GetLink(link1).GetIndex()]
    trns2 = robot.GetLinkTransformations()[robot.GetLink(link2).GetIndex()]
    return np.dot(np.linalg.inv(trns2),trns1)       



def discretizePath(poses, resolution):
    waypoints = []
    for i in range(len(poses)-1):
        mid_waypoints = createWayPoints(poses[i], poses[i+1], int(resolution/(len(poses)-1)))
        for j in mid_waypoints:
            waypoints.append(j)
    return waypoints

def createWayPoints(start_pose, end_pose, resolution):
    poses=[]
    int_pose = (start_pose - end_pose)/resolution
    first_pose = start_pose
    poses.append(first_pose)
    for i in range(resolution-1):
        first_pose = first_pose - int_pose
        poses.append(first_pose)
    return poses


#excuting Path

def getVel(start_pose, end_pose, unitTime = 1.0):
    return (end_pose-start_pose)/unitTime


def boundVelocity(robot, velocity, joint_velocity_limits=None):
    manip = robot.GetActiveManipulator()
    robot.SetActiveDOFs(manip.GetArmIndices())
    if joint_velocity_limits is None:
        joint_velocity_limits = robot.GetActiveDOFMaxVel()
    elif joint_velocity_limits:
        joint_velocity_limits = numpy.array([numpy.PINF] * robot.GetActiveDOF())
    new_vel = []
    for i in range(len(joint_velocity_limits)):
        if velocity[i] > joint_velocity_limits[i]:
            new_vel.append(joint_velocity_limits[i])
        else:
            new_vel.append(velocity[i])
    return numpy.array(new_vel)


def boundJointLimits(robot, q_curr, joint_limit_tolerance=3e-2):
    q_min, q_max = robot.GetActiveDOFLimits()
    new_joint_value = []
    for i in range(len(q_curr)):
        if(q_curr[i]>q_max[i]):
            new_joint_value.append(q_max[i])
        elif(q_curr[i]<q_min[i]):
            new_joint_value.append(q_min[i])
        else:
            new_joint_value.append(q_curr[i])
    return numpy.array(new_joint_value)


def calculateJacobianArm(robot):
    manip = robot.GetActiveManipulator()
    jacob_spatial = manip.CalculateJacobian()
    jacob_angular = manip.CalculateAngularVelocityJacobian()
    jacob = numpy.vstack((jacob_spatial, jacob_angular))
    return jacob

def calculateJacobianBase(robot):
    l = 0.37476
    r = 0.0125
    jacob_base = np.array(([r/2, r/2],[0,0],[0,0],[0,0],[0,0],[-r/l,r/l]))
    return jacob_base

def calculateFullJacobian(robot):
    jacob_arm = calculateJacobianArm(robot)
    jacob_base = calculateJacobianBase(robot)
    full_jacob = numpy.hstack((jacob_arm, jacob_base))
    return full_jacob

def isInLimit(vel, bounds):
    for i in range(len(vel)):
        if(vel[i]>=bounds[i][1] or vel[i]<=bounds[i][0]):
            return False

def calculateBaseGoalCart(q_dot_base):
    jacob_base = calculateJacobianBase(robot)
    cart_vel = numpy.dot(jacob_base, q_dot_base)
    TeeBase = robot.GetTransform()
    trns = matrixFromAxisAngle([0,0,cart_vel[-1]])
    trns[0:3, 3] = [cart_vel[0],0, 0]
    goal = np.dot(TeeBase,trns)
    transl = goal[0:3, 3]
    angles = axisAngleFromRotationMatrix(goal)
    cart_vel2 = [transl[0],0.0, angles[-1]]
    return cart_vel2

def getQDot(robot, pose, unitTime = 1.0, joint_velocity_limits=None):
    joint_limit_tolerance=3e-2
    manip = robot.GetActiveManipulator()
    vel = getVel(transformToPose(manip.GetEndEffectorTransform()), pose)
    full_jacob = calculateFullJacobian(robot)
    jacob_inv = np.linalg.pinv(full_jacob)
    q_dot = numpy.dot(jacob_inv, vel)
    if joint_velocity_limits is None:
        joint_velocity_limits = robot.GetActiveDOFMaxVel()
    elif joint_velocity_limits:
        joint_velocity_limits = numpy.array([numpy.PINF] * robot.GetActiveDOF())
    bounds = numpy.column_stack((-joint_velocity_limits, joint_velocity_limits))
    q_curr = robot.GetActiveDOFValues()
    q_min, q_max = robot.GetActiveDOFLimits()
    dq_bounds = [
        (0., max) if q_curr[i] <= q_min[i] + joint_limit_tolerance else
        (min, 0.) if q_curr[i] >= q_max[i] - joint_limit_tolerance else
        (min, max) for i, (min, max) in enumerate(bounds)]
    q_dot_arm = q_dot[:-2]
    curr_values = manip.GetArmDOFValues()
    changed_values = curr_values + q_dot_arm * 1.0
    value = 0
    while(isInLimit(changed_values,dq_bounds) == False):
        value = value+1
        
        for i in range(len(q_dot_arm)):
            if(changed_values[i]>=dq_bounds[i][1]-joint_limit_tolerance or changed_values[i]<=dq_bounds[i][0]+joint_limit_tolerance):
                full_jacob[:,i] = 0.0
                jacob_inv = np.linalg.pinv(full_jacob)
                q_dot = numpy.dot(jacob_inv, vel)
                q_dot_arm = q_dot[:-2]
                curr_values = manip.GetArmDOFValues()
                changed_values = curr_values + q_dot_arm * unitTime
    return q_dot

    
def getGoalToExecute(robot, pose, unitTime):
    manip = robot.GetActiveManipulator()
    q_dot = getQDot(robot, pose, unitTime,joint_velocity_limits=numpy.PINF)
    q_dot_arm = q_dot[:-2]*0.25
    
    q_dot_base = q_dot[-2:]
    curr_values = manip.GetArmDOFValues()
    changed_values = curr_values + q_dot_arm * unitTime
    cart_vel = calculateBaseGoalCart(q_dot_base)
    finalgoal = numpy.hstack((changed_values, cart_vel))
    return finalgoal
    
def getTransform(link_name):
    return robot.GetLinkTransformations()[robot.GetLink(link_name).GetIndex()]


def transformToPose(transform):
    transl = transform[0:3, 3]
    angles = axisAngleFromRotationMatrix(transform)
    pose = np.append(transl, angles)
    return pose

def poseToTransform(pose):
    angles = pose[3:6]
    trns = matrixFromAxisAngle(angles)
    trns[0:3, 3] = pose[0:3]
    return trns

red = [1,0,0,1]
blue = [0,0,1,1]
pink = [1,0,0.5,1]
yellow = [1,1,0,1]
handles = []

    
def executeVelPath(robot, pose, handles,unitTime = 1.0,joint_velocity_limits=None):
    manip = robot.SetActiveManipulator('arm')
    basemanip = interfaces.BaseManipulation(robot)
    with robot:
        Tee = manip.GetEndEffectorTransform()
        Tee_gripper = getTransform('gripper_link')
        TeeBase = robot.GetTransform()
        jointnames=['shoulder_pan_joint','shoulder_lift_joint','upperarm_roll_joint','elbow_flex_joint','forearm_roll_joint','wrist_flex_joint','wrist_roll_joint']
        robot.SetActiveDOFs([robot.GetJoint(name).GetDOFIndex() for name in jointnames],DOFAffine.X|DOFAffine.Y|DOFAffine.RotationAxis)
        robot.SetAffineTranslationMaxVels([0.5,0.5,0.5])
        robot.SetAffineRotationAxisMaxVels(np.ones(4))

        finalgoal = getGoalToExecute(robot, pose, unitTime)
        print 'I am here'
        #print finalgoal
        #q_arm = finalgoal[1:8]
        #robot.arm.PlanToConfiguration(q_arm, execute = True) 
        basemanip.MoveActiveJoints(goal=finalgoal,maxiter=5000,steplength=1,maxtries=2)
        #print finalgoal
        #print 'size of finalgoal:'+str(len(finalgoal))
        imd_goal = []
        for i in range(len(finalgoal)):
        	if i != (8):
        		imd_goal.append(finalgoal[i]) 

        print imd_goal
        finalgoal2 =[]
        for i in range(len(imd_goal)):
        	if i>6:
        		finalgoal2.append(imd_goal[i]/20.0)
        	else:
        		finalgoal2.append(imd_goal[i])


        robot.whole_body.Move(finalgoal2)
        
        pl = plottingPoints(robot.GetEnv(),handles)
        pl.plotPoint(transformToPose(TeeBase), 0.01, yellow)
        pl.plotPoint(transformToPose(Tee_gripper), 0.01, blue)
    waitrobot(robot)
    return transformToPose(Tee), transformToPose(TeeBase)
    
def executePath(robot, path, resolution, handles):
    print 'path: '+str(len(path))
    dis_poses = discretizePath(path, resolution)
    print 'dis_poses: '+str(len(dis_poses))
    
    #pl.plotPoints(path, 0.005, pink)
    poses = []
    base_poses = []
    all_poses = []
    all_poses.append(dis_poses)
    for i in range(len(dis_poses)-1):
        #print ">>>>>>>>Iteration "+str(i)+"<<<<<<<<<<<<<<"
        pose, base_pose = executeVelPath(robot, dis_poses[i+1], handles)
        poses.append(pose)
        base_poses.append(base_pose)
    all_poses.append(poses) 
    all_poses.append(base_poses) 
    return all_poses
         

def executePathTransform(robot, transforms, resolution, handles):
    poses = []
    for i in range(len(transforms)):
        poses.append(transformToPose(transforms[i]))
    executePath(robot, poses, resolution, handles)


if __name__ == '__main__':
	rospy.init_node('fetchpy')
	fetch_args = {'sim':True, 'viewer':'qtcoin'}
	env, robot = fetchpy.initialize(**fetch_args)
	viewer = env.GetViewer()
	originaxes = misc.DrawAxes(env, [1,0,0,0,0,0,0], dist = 1, linewidth= 2)
	keep_going = True
	while keep_going:

		# with env:
		# 	body = RaveCreateKinBody(env,'')
		# 	body.SetName('testbody3')
		# 	body.InitFromBoxes(numpy.array([[0.65,0,0.48,0.04,0.02,0.03]]),True) 
		# 	env.AddKinBody(body)
		# 	time.sleep(4)

		# raw_input("Press enter to continue...")

		# target = env.GetKinBody('testbody3')
		# robot.gripper.OpenHand()


		# raw_input("Press enter to continue...")


		angle2 = ([0.0, -0.65, 0.0, 1.5, 0.0, 0.68, 0.0]) 
		robot.arm.PlanToConfiguration(angle2, execute = True) 

		raw_input("Press enter to continue...")

		# robot.gripper.CloseHand()
		# with env:
		# 	robot.Grab(env.GetKinBody('testbody3'))

		raw_input("Press enter to continue...")

		to_Table = ([0.70503065,-0.81321057,0.44084394,1.52903305,-0.37976212,0.92392059,0.8291418])
		robot.arm.PlanToConfiguration(to_Table, execute = True) 

		raw_input("Press enter to continue...")


		poses = createPattern(transformToPose(getTransform('gripper_link')),200,4)
		pl = plottingPoints(robot.GetEnv(),handles)
		pl.plotPoints(poses, 0.005, pink)
		stat_trns = getTransformBetweenLinks('wrist_roll_link','gripper_link')
		new_poses = []
		for i in poses:
			trns = poseToTransform(i)
			new_trns = np.dot(trns,stat_trns)
			new_poses.append(transformToPose(new_trns))
		handles =[]
		all_poses = executePath(robot, new_poses, 500, handles)
		
		

		raw_input("Press enter to continue...")

		print 'I am OK'






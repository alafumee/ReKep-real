"""
Adapted from OmniGibson and the Lula IK solver
"""
# import omnigibson.lazy as lazy
import numpy as np


import numpy as np
from scipy.spatial.transform import Rotation
from polymetis import RobotInterface

import torch
import torchcontrol as toco
from torchcontrol.transform import Rotation as R
from torchcontrol.transform import Transformation as T

class IKResult:
    """Class to store IK solution results"""
    def __init__(self, success, joint_positions, error_pos, error_rot, num_descents=None):
        self.success = success
        self.cspace_position = joint_positions
        self.position_error = error_pos
        self.rotation_error = error_rot
        self.num_descents = num_descents if num_descents is not None else 1
    
    
# TODO use real IK solver
class FrankaIKSolver:
    """Franka IK Solver"""
    def __init__(self, reset_joint_pos, world2robot_homo=None):
        # DH parameters for Franka (simplified version)
        self.dh_params = {
            'd1': 0.333,   # Joint 1
            'd3': 0.316,   # Joint 3
            'd5': 0.384,   # Joint 5
            'd7': 0.107,   # End effector
            'a7': 0.088,   # End effector
        }
        
        # Joint limits (in radians)
        self.joint_limits = {
            'lower': np.array([-2.8973, -1.7628, -2.8973, -3.0718, -2.8973, -0.0175, -2.8973]),
            'upper': np.array([2.8973, 1.7628, 2.8973, -0.0698, 2.8973, 3.7525, 2.8973])
        }
        
        # Default home position
        self.reset_joint_pos = reset_joint_pos
        
        # Transform from world to robot base
        self.world2robot_homo = world2robot_homo if world2robot_homo is not None else np.eye(4)

    def transform_pose(self, pose_homo):
        """Transform pose from world frame to robot base frame"""
        return np.dot(self.world2robot_homo, pose_homo)
    
    def solve(self, target_pose_homo, 
             position_tolerance=0.01,
             orientation_tolerance=0.05,
             max_iterations=150,
             initial_joint_pos=None):
        """
        Mock IK solver that returns a valid IKResult
        """
        # Transform target pose to robot base frame
        robot_pose = self.transform_pose(target_pose_homo)
        
        # Extract position and rotation
        target_pos = robot_pose[:3, 3]
        target_rot = robot_pose[:3, :3]
        
        # Use initial joint positions or default
        if initial_joint_pos is None:
            initial_joint_pos = self.reset_joint_pos
        
        # 简单的工作空间检查
        in_workspace = np.all(np.abs(target_pos) < 1.0)
        
        if 1: #in_workspace:
            # 成功情况
            return IKResult(
                success=True,
                joint_positions=initial_joint_pos,  # 使用初始关节角度或默认值
                error_pos=0.01,
                error_rot=0.01,
                num_descents=max_iterations // 2
            )
        else:
            # 失败情况，但仍然返回一个有效的IKResult
            return IKResult(
                success=False,
                joint_positions=self.reset_joint_pos,  # 使用重置位置
                error_pos=1.0,
                error_rot=1.0,
                num_descents=max_iterations
            )
    
    def forward_kinematics(self, joint_positions):
        """
        Compute forward kinematics (placeholder)
        
        Args:
            joint_positions (array): Joint angles
            
        Returns:
            4x4 array: Homogeneous transformation matrix
        """
        # Placeholder - implement actual FK
        return np.eye(4)

# Unit tests
def test_franka_ik():
    # Create solver
    solver = FrankaIKSolver()
    
    # Test case 1: Identity pose
    target = np.eye(4)
    result = solver.solve(target)
    assert result['success']
    
    # Test case 2: Transformed pose
    target = np.array([
        [1, 0, 0, 0.5],
        [0, 1, 0, 0.0],
        [0, 0, 1, 0.3],
        [0, 0, 0, 1.0]
    ])
    result = solver.solve(target)
    assert result['success']
    
    # Test case 3: Check joint limits
    joints = result['joint_positions']
    assert np.all(joints >= solver.joint_limits['lower'])
    assert np.all(joints <= solver.joint_limits['upper'])
    
    print("All tests passed!")

class PolyFrankaIKSolver:
    '''
    Franka IK solver wrapped on polymetis RobotInterface interfaces
    '''
    def __init__(self, robot_interface:RobotInterface, world2robot_homo, reset_joint_pos):
        self.robot = robot_interface
        self.world2robot_homo = world2robot_homo if world2robot_homo is not None else np.eye(4)
        
    def solve(self, target_pose_homo, max_iterations, initial_joint_pos, tol=1e-3):
        # Transform target pose to robot base frame
        robot_pose = self.transform_pose(target_pose_homo)
        
        # Extract position and rotation
        target_pos = robot_pose[:3, 3]
        target_rot = robot_pose[:3, :3]
        # Convert rotation matrix to quaternion
        target_quat = R.from_matrix(target_rot).as_quat()
        
        joint_pos_output = self.robot.robot_model.inverse_kinematics(
            target_pos, target_quat, rest_pose=initial_joint_pos,
            max_iters=max_iterations
        )

        # Check result
        pos_output, quat_output = self.robot.robot_model.forward_kinematics(joint_pos_output)
        pose_desired = T.from_rot_xyz(R.from_quat(target_quat), target_pos)
        pose_output = T.from_rot_xyz(R.from_quat(quat_output), pos_output)
        err = torch.linalg.norm((pose_desired * pose_output.inv()).as_twist())
        ik_sol_found = err < tol

        # TODO: check workspace
        in_workspace = np.all(np.abs(target_pos) < 1.0)
        
        # TODO: decompose error to pos and rot
        if ik_sol_found:
            return IKResult(
                    success=True,
                    joint_positions=joint_pos_output,
                    error_pos=err,
                    error_rot=err,
                    num_descents=0,
            )
        else:
            return IKResult(
                success=False,
                joint_positions=self.robot.home_pose,  # 使用重置位置
                error_pos=1.0,
                error_rot=1.0,
                num_descents=max_iterations
            )
        

class IKSolver:
    """
    Class for thinly wrapping Lula IK solver
    
    This class implements inverse kinematics (IK) for robotic manipulators.
    IK is the process of calculating joint angles needed to achieve a desired
    end-effector pose. This is essential for robot motion planning and control.
    
    The solver uses Cyclic Coordinate Descent (CCD), an iterative method that:
    1. Optimizes one joint at a time
    2. Minimizes position and orientation error of end-effector
    3. Respects joint limits and collision constraints
    4. Handles redundant manipulators (robots with >6 DOF)
    """

    def __init__(
        self,
        robot_description_path,
        robot_urdf_path,
        eef_name,
        reset_joint_pos,
        world2robot_homo,
    ):
        # Create robot description, kinematics, and config
        # self.robot_description = lazy.lula.load_robot(robot_description_path, robot_urdf_path)
        # self.kinematics = self.robot_description.kinematics()
        # self.config = lazy.lula.CyclicCoordDescentIkConfig()
        self.eef_name = eef_name
        self.reset_joint_pos = reset_joint_pos
        self.world2robot_homo = world2robot_homo

    def solve(
        self,
        target_pose_homo,
        position_tolerance=0.01,
        orientation_tolerance=0.05,
        position_weight=1.0,
        orientation_weight=0.05,
        max_iterations=150,
        initial_joint_pos=None,
    ):
        """
        Backs out joint positions to achieve desired @target_pos and @target_quat

        The solver uses an optimization approach to find joint angles that place the
        end-effector at the target pose. It balances:
        - Position accuracy (xyz coordinates)
        - Orientation accuracy (rotation matrix)
        - Joint limits
        - Solution convergence speed

        Args:
            target_pose_homo (np.ndarray): [4, 4] homogeneous transformation matrix of the target pose in world frame
            position_tolerance (float): Maximum position error (L2-norm) for a successful IK solution
            orientation_tolerance (float): Maximum orientation error (per-axis L2-norm) for a successful IK solution
            position_weight (float): Weight for the relative importance of position error during CCD
            orientation_weight (float): Weight for the relative importance of position error during CCD
            max_iterations (int): Number of iterations used for each cyclic coordinate descent.
            initial_joint_pos (None or n-array): If specified, will set the initial cspace seed when solving for joint
                positions. Otherwise, will use self.reset_joint_pos

        Returns:
            ik_results (lazy.lula.CyclicCoordDescentIkResult): IK result object containing the joint positions and other information.
        """
        # convert target pose to robot base frame
        # target_pose_robot = np.dot(self.world2robot_homo, target_pose_homo)
        # target_pose_pos = target_pose_robot[:3, 3]
        # target_pose_rot = target_pose_robot[:3, :3]
        # ik_target_pose = lazy.lula.Pose3(lazy.lula.Rotation3(target_pose_rot), target_pose_pos)
        # Set the cspace seed and tolerance
        initial_joint_pos = self.reset_joint_pos if initial_joint_pos is None else np.array(initial_joint_pos)
        # self.config.cspace_seeds = [initial_joint_pos]
        # self.config.position_tolerance = position_tolerance
        # self.config.orientation_tolerance = orientation_tolerance
        # self.config.ccd_position_weight = position_weight
        # self.config.ccd_orientation_weight = orientation_weight
        # self.config.max_num_descents = max_iterations
        # Compute target joint positions
        return None
        # ik_results = lazy.lula.compute_ik_ccd(self.kinematics, ik_target_pose, self.eef_name, self.config)
        # return ik_results


if __name__ == "__main__":
    test_franka_ik()

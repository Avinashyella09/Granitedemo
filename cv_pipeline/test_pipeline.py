import cv2
import numpy as np
import os
import sys

# Ensure current directory is on path to import local cv_pipeline modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cv_pipeline.detector import MarkerDetector
from cv_pipeline.estimator import VolumeEstimator
from cv_pipeline.pipeline import GraniteCVPipeline

def generate_synthetic_test_image(output_path="synthetic_block.png"):
    """
    Generates a synthetic test image with a gray block and two ArUco markers.
    Marker 1 (Primary): ID 1
    Marker 2 (Side): ID 2
    """
    # 800 x 600 background (quarry floor color / dirt color)
    img = np.ones((600, 800, 3), dtype=np.uint8) * 180 # Light gray background
    
    # Draw a simulated granite block (gray cuboid)
    # Front/Primary face: (150, 150) to (650, 450)
    cv2.rectangle(img, (150, 150), (650, 450), (100, 100, 100), -1)
    # Side face projection: (650, 150) to (750, 450)
    pts = np.array([[650, 150], [750, 100], [750, 400], [650, 450]], dtype=np.int32)
    cv2.fillPoly(img, [pts], (80, 80, 80))
    # Top face projection: (150, 150) to (250, 100) to (750, 100) to (650, 150)
    pts_top = np.array([[150, 150], [250, 100], [750, 100], [650, 150]], dtype=np.int32)
    cv2.fillPoly(img, [pts_top], (120, 120, 120))
    
    # Generate ArUco markers (ID 1 and ID 2)
    try:
        aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_50)
        generate_marker = cv2.aruco.generateImageMarker
    except AttributeError:
        aruco_dict = cv2.aruco.Dictionary_get(cv2.aruco.DICT_5X5_50)
        def generate_marker(d, m_id, size):
            m = np.zeros((size, size), dtype=np.uint8)
            cv2.aruco.drawMarker(d, m_id, size, m, 1)
            return m

    # Create Primary Marker (ID 1), 100x100 pixels
    m1 = generate_marker(aruco_dict, 1, 100)
    m1_color = cv2.cvtColor(m1, cv2.COLOR_GRAY2BGR)
    # Paste Marker 1 on the front face (e.g. at (200, 200))
    img[200:300, 200:300] = m1_color
    
    # Create Side Marker (ID 2), 100x100 pixels
    m2 = generate_marker(aruco_dict, 2, 100)
    m2_color = cv2.cvtColor(m2, cv2.COLOR_GRAY2BGR)
    
    # Paste Marker 2 on the side face (with a warp to simulate perspective rotation)
    # Destination points on the side face
    dst_pts = np.array([[660, 200], [740, 170], [740, 290], [660, 320]], dtype=np.float32)
    src_pts = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], dtype=np.float32)
    H = cv2.getPerspectiveTransform(src_pts, dst_pts)
    warp_m2 = cv2.warpPerspective(m2_color, H, (800, 600))
    
    # Blend warped side marker into image using a convex poly mask
    mask_m2 = np.zeros((600, 800), dtype=np.uint8)
    cv2.fillConvexPoly(mask_m2, dst_pts.astype(np.int32), 255)
    img[mask_m2 > 0] = warp_m2[mask_m2 > 0]
    
    cv2.imwrite(output_path, img)
    print(f"Generated synthetic test image: {output_path}")

    # Also return the ground truth mask for verification (Front/Primary + Side + Top faces)
    gt_mask = np.zeros((600, 800), dtype=np.uint8)
    cv2.rectangle(gt_mask, (150, 150), (650, 450), 255, -1)
    cv2.fillPoly(gt_mask, [pts], 255)
    cv2.fillPoly(gt_mask, [pts_top], 255)
    return output_path, gt_mask

def run_tests():
    print("=== RUNNING CV PIPELINE TESTS ===")
    img_path, gt_mask = generate_synthetic_test_image()

    # Test Detector
    print("\n--- Testing Marker Detector ---")
    detector = MarkerDetector(marker_size_cm=20.0)
    img = cv2.imread(img_path)
    markers = detector.detect(img)
    print(f"Detected markers: {list(markers.keys())}")
    
    if 1 in markers and 2 in markers:
        print("Success: Both markers 1 (Primary) and 2 (Side) successfully detected!")
    else:
        print("Error: Failed to detect one or both markers.")
        return

    # Test Estimator with ground truth mask
    print("\n--- Testing Volume Estimator with GT Mask ---")
    estimator = VolumeEstimator(primary_marker_id=1, side_marker_id=2)
    result = estimator.estimate(gt_mask, markers, density_mt_per_m3=2.7)
    
    print("Estimation Results:")
    for k, v in result.items():
        print(f"  {k}: {v}")

    # Test Single Marker / Fail condition (only pass primary marker)
    print("\n--- Testing Single-Marker Geometry Error/Low Confidence State ---")
    single_marker_dict = {1: markers[1]}
    single_result = estimator.estimate(gt_mask, single_marker_dict, density_mt_per_m3=2.7)
    print("Single Marker Results:")
    for k, v in single_result.items():
        print(f"  {k}: {v}")
    
    if single_result['status'] == 'error':
        print("Success: Properly returned error state when depth cannot be established.")
    else:
        print("Warning: Did not fail on single marker with no manual input.")

    # Run full orchestrator pipeline (loads YOLOv8 model)
    print("\n--- Running Full Pipeline Orchestrator (YOLOv8-seg) ---")
    print("NOTE: The generic 'yolov8n-seg.pt' model loaded here is for interface integration testing only.")
    print("It is trained on COCO (persons, cars, suitcases) and does not segment granite blocks.")
    print("A custom-trained YOLOv8-seg model is required for real-world granite block segmentation.")
    try:
        pipeline = GraniteCVPipeline(model_path="yolov8n-seg.pt")
        pipeline_result = pipeline.process_image(img_path)
        
        print("Pipeline Result:")
        for k, v in pipeline_result.items():
            if k != 'annotated_image':
                print(f"  {k}: {v}")
                
        if pipeline_result['annotated_image'] is not None:
            cv2.imwrite("annotated_synthetic_output.png", pipeline_result['annotated_image'])
            print("Successfully saved annotated visual output to: annotated_synthetic_output.png")
    except Exception as e:
        print(f"YOLOv8 pipeline run encountered exception (typical if download/init is pending): {e}")

if __name__ == "__main__":
    run_tests()

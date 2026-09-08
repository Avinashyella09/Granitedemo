import cv2
import numpy as np
from .detector import MarkerDetector
from .segmentor import BlockSegmentor
from .estimator import VolumeEstimator

class GraniteCVPipeline:
    def __init__(self, model_path="yolov8n-seg.pt", marker_size_cm=20.0, primary_marker_id=1, side_marker_id=2):
        self.detector = MarkerDetector(marker_size_cm=marker_size_cm)
        self.segmentor = BlockSegmentor(model_path=model_path)
        self.estimator = VolumeEstimator(primary_marker_id=primary_marker_id, side_marker_id=side_marker_id)

    def process_image(self, image_path, density_mt_per_m3=2.7):
        """
        Processes a single granite block image.
        
        Args:
            image_path: Path to the image file.
            density_mt_per_m3: Density of the granite.
            
        Returns:
            dict containing measurement results and 'annotated_image' (BGR numpy array with visual overlays).
        """
        # Load image
        img = cv2.imread(image_path)
        if img is None:
            return {
                'status': 'error',
                'error_message': f'Failed to load image from path: {image_path}',
                'confidence': 0.0,
                'annotated_image': None
            }

        # 1. Detect markers
        markers = self.detector.detect(img)
        marker_centers = [m['center'] for m in markers.values()]

        # 2. Segment block
        mask = self.segmentor.segment(img, marker_centers=marker_centers)

        # 3. Estimate dimensions
        result = self.estimator.estimate(
            mask=mask, 
            markers=markers, 
            density_mt_per_m3=density_mt_per_m3
        )

        # 4. Generate visual annotation overlay
        annotated_img = img.copy()
        
        # Overlay block segmentation mask in semi-transparent green
        if mask is not None:
            mask_indices = mask > 0
            overlay = annotated_img.copy()
            overlay[mask_indices] = [0, 255, 0] # Solid green
            # Blend 30% green overlay with 70% original image
            cv2.addWeighted(overlay, 0.3, annotated_img, 0.7, 0, annotated_img)
            
            # Find and draw block contour outline in bright green
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if contours:
                cv2.drawContours(annotated_img, contours, -1, (0, 255, 0), 2)

        # Highlight detected markers with cyan bounding boxes and IDs
        for marker_id, marker_info in markers.items():
            corners = marker_info['corners'].astype(np.int32)
            cv2.polylines(annotated_img, [corners], isClosed=True, color=(255, 255, 0), thickness=2)
            # Label marker
            cx, cy = int(marker_info['center'][0]), int(marker_info['center'][1])
            cv2.putText(annotated_img, f"ID: {marker_id}", (cx - 20, cy - 10), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)

        # Render measurement stats overlay
        y_offset = 30
        if result['status'] == 'success':
            stats = [
                f"Status: Success",
                f"Length: {result['length_m']:.3f} m",
                f"Height: {result['height_m']:.3f} m",
                f"Breadth: {result['breadth_m']:.3f} m",
                f"Volume: {result['volume_m3']:.4f} m3",
                f"Est. Weight: {result['tonnage_mt']:.3f} MT",
                f"Confidence: {result['confidence']:.2f}"
            ]
            box_color = (0, 100, 0) # Dark green
            text_color = (255, 255, 255)
        else:
            stats = [
                f"Status: ERROR",
                f"Error: {result['error_message']}"
            ]
            box_color = (0, 0, 150) # Dark red
            text_color = (200, 200, 255)

        # Draw beautiful overlay background card on the top left
        max_txt_w = max([cv2.getTextSize(t, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)[0][0] for t in stats])
        card_h = len(stats) * 25 + 15
        cv2.rectangle(annotated_img, (10, 10), (20 + max_txt_w, 10 + card_h), box_color, -1)
        # Transparent blend
        cv2.addWeighted(annotated_img, 0.85, annotated_img, 0.15, 0, annotated_img)

        # Draw overlay text
        for line in stats:
            cv2.putText(annotated_img, line, (15, y_offset), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, text_color, 2)
            y_offset += 25

        result['annotated_image'] = annotated_img
        return result

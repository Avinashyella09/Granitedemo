import numpy as np
from ultralytics import YOLO
import cv2

class BlockSegmentor:
    def __init__(self, model_path="yolov8n-seg.pt"):
        """
        Initializes the YOLOv8-seg segmentor.
        """
        self.model = YOLO(model_path)

    def segment(self, image, marker_centers=None):
        """
        Segments the image using YOLOv8-seg and returns the best mask for the granite block.
        
        Args:
            image: input BGR image.
            marker_centers: list of coordinates (x, y) of detected markers to help identify 
                            the correct block segment if multiple objects are detected.
                            
        Returns:
            binary mask of the segmented block (numpy array of shape (H, W), dtype uint8, values 0 or 255), 
            or None if no block is detected.
        """
        # Run YOLO inference
        # verbose=False to keep logs clean
        results = self.model(image, verbose=False)
        
        if not results or len(results) == 0:
            return None
            
        result = results[0]
        if result.masks is None or len(result.masks) == 0:
            return None
            
        masks_data = result.masks.data.cpu().numpy() # shape (N, H, W)
        h_orig, w_orig = image.shape[:2]
        
        best_mask = None
        max_score = -1
        
        for i, mask in enumerate(masks_data):
            # Resize mask to original image size
            mask_resized = cv2.resize(mask, (w_orig, h_orig), interpolation=cv2.INTER_NEAREST)
            mask_bin = (mask_resized > 0.5).astype(np.uint8) * 255
            
            # Area of mask
            area = np.sum(mask_bin > 0)
            if area == 0:
                continue
                
            score = area # Primary heuristic: largest area
            
            # If marker centers are provided, prefer the mask containing or closest to the markers
            if marker_centers:
                for mc in marker_centers:
                    x, y = int(mc[0]), int(mc[1])
                    if 0 <= x < w_orig and 0 <= y < h_orig:
                        # If marker is inside the mask, boost its score heavily
                        if mask_bin[y, x] > 0:
                            score += w_orig * h_orig * 10
                            
            if score > max_score:
                max_score = score
                best_mask = mask_bin
                
        return best_mask
